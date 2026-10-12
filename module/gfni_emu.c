// SPDX-License-Identifier: GPL-2.0
/*
 * gfni_emu.ko - Kernel-level trap & emulation for GFNI, VPCLMULQDQ, AVX-VNNI, and VAES
 * Intercepts #UD (Invalid Opcode, Vector 6) from user mode via register_die_notifier.
 */

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/init.h>
#include <linux/kdebug.h>
#include <linux/uaccess.h>
#include <asm/ptrace.h>
#include <asm/traps.h>

MODULE_LICENSE("GPL");
MODULE_AUTHOR("fukayatti");
MODULE_DESCRIPTION("GFNI, VPCLMULQDQ, VAES, and Vector ISA Trap & Emulation Module");

static inline uint8_t gf_parity8(uint8_t v)
{
	v ^= v >> 4;
	v ^= v >> 2;
	v ^= v >> 1;
	return v & 1;
}

/*
 * 1-byte GF(2) affine transform, as in the Intel SDM:
 *   out.bit[i] = parity(matrix.byte[7 - i] & x) ^ imm.bit[i]
 * (Output bit i uses matrix byte 7-i as a mask over *all* input bits.)
 */
static inline uint8_t gf2p8_affine_byte(uint64_t matrix_qword, uint8_t src1_byte, uint8_t imm)
{
	uint8_t dest = 0;
	int i;

	for (i = 0; i < 8; i++) {
		uint8_t row = (matrix_qword >> (8 * (7 - i))) & 0xff;

		dest |= (uint8_t)((gf_parity8(row & src1_byte) ^ ((imm >> i) & 1)) << i);
	}
	return dest;
}

/* GF(2^8) Galois Field multiplication modulo 0x11B (AES polynomial) */
static inline uint8_t gf2p8_mul(uint8_t a, uint8_t b)
{
	uint8_t res = 0;
	while (b) {
		if (b & 1)
			res ^= a;
		a = (a << 1) ^ ((a & 0x80) ? 0x1b : 0);
		b >>= 1;
	}
	return res;
}

/* GF(2^8) multiplicative inverse table */
static uint8_t gf2p8_inv_table[256];

static void init_gf2p8_tables(void)
{
	int a, b;
	gf2p8_inv_table[0] = 0;
	for (a = 1; a < 256; a++) {
		for (b = 1; b < 256; b++) {
			if (gf2p8_mul(a, b) == 1) {
				gf2p8_inv_table[a] = b;
				break;
			}
		}
	}
}

/* 64-bit carry-less multiplication producing 128-bit result */
static inline void clmul64(uint64_t a, uint64_t b, uint64_t *res_lo, uint64_t *res_hi)
{
	uint64_t lo = 0, hi = 0;

	while (b) {
		int i = __builtin_ctzll(b);
		lo ^= (a << i);
		if (i > 0)
			hi ^= (a >> (64 - i));
		b &= (b - 1);
	}
	*res_lo = lo;
	*res_hi = hi;
}

/* Hardware 128-bit AES round helpers */
static inline void do_aesenc(uint8_t *dest, const uint8_t *src1, const uint8_t *src2)
{
	__asm__ __volatile__ (
		".arch default\n\t"
		"movdqu (%1), %%xmm0\n\t"
		"movdqu (%2), %%xmm1\n\t"
		"aesenc %%xmm1, %%xmm0\n\t"
		"movdqu %%xmm0, (%0)"
		:: "r"(dest), "r"(src1), "r"(src2) : "memory"
	);
}

static inline void do_aesenclast(uint8_t *dest, const uint8_t *src1, const uint8_t *src2)
{
	__asm__ __volatile__ (
		".arch default\n\t"
		"movdqu (%1), %%xmm0\n\t"
		"movdqu (%2), %%xmm1\n\t"
		"aesenclast %%xmm1, %%xmm0\n\t"
		"movdqu %%xmm0, (%0)"
		:: "r"(dest), "r"(src1), "r"(src2) : "memory"
	);
}

static inline void do_aesdec(uint8_t *dest, const uint8_t *src1, const uint8_t *src2)
{
	__asm__ __volatile__ (
		".arch default\n\t"
		"movdqu (%1), %%xmm0\n\t"
		"movdqu (%2), %%xmm1\n\t"
		"aesdec %%xmm1, %%xmm0\n\t"
		"movdqu %%xmm0, (%0)"
		:: "r"(dest), "r"(src1), "r"(src2) : "memory"
	);
}

static inline void do_aesdeclast(uint8_t *dest, const uint8_t *src1, const uint8_t *src2)
{
	__asm__ __volatile__ (
		".arch default\n\t"
		"movdqu (%1), %%xmm0\n\t"
		"movdqu (%2), %%xmm1\n\t"
		"aesdeclast %%xmm1, %%xmm0\n\t"
		"movdqu %%xmm0, (%0)"
		:: "r"(dest), "r"(src1), "r"(src2) : "memory"
	);
}

/* Signed 32-bit saturating addition */
static inline int32_t sat_add32(int32_t a, int32_t b)
{
	int64_t res = (int64_t)a + (int64_t)b;
	if (res > 2147483647LL) return 2147483647;
	if (res < -2147483648LL) return -2147483648LL;
	return (int32_t)res;
}

/* Read GPR from struct pt_regs */
static unsigned long get_gpr(struct pt_regs *regs, int num)
{
	switch (num) {
	case 0: return regs->ax;
	case 1: return regs->cx;
	case 2: return regs->dx;
	case 3: return regs->bx;
	case 4: return regs->sp;
	case 5: return regs->bp;
	case 6: return regs->si;
	case 7: return regs->di;
	case 8: return regs->r8;
	case 9: return regs->r9;
	case 10: return regs->r10;
	case 11: return regs->r11;
	case 12: return regs->r12;
	case 13: return regs->r13;
	case 14: return regs->r14;
	case 15: return regs->r15;
	default: return 0;
	}
}

/*
 * The die notifier runs with IRQs off, so a page fault here must never sleep to
 * bring a page in. Read with page faults disabled; a non-resident page just
 * fails the read (the caller then lets the normal SIGILL path run).
 */
static int read_user_nofault(void *dst, unsigned long src, size_t len)
{
	unsigned long left;

	pagefault_disable();
	left = copy_from_user(dst, (const void __user *)src, len);
	pagefault_enable();
	return left ? -EFAULT : 0;
}

/*
 * Fetch up to 15 instruction bytes at ip. The bytes after the instruction may sit
 * on an unmapped page, so retry with a shorter read; the shortest encoding we decode
 * is 5 bytes (VEX3 + opcode + modrm).
 */
static int fetch_insn(uint8_t *insn, unsigned long ip)
{
	int len;

	memset(insn, 0, 16);
	for (len = 15; len >= 5; len--)
		if (!read_user_nofault(insn, ip, len))
			return len;		/* number of valid bytes in insn[] */
	return -EFAULT;
}

/*
 * Decode and emulate an instruction at regs->ip
 * ymm points to the 16 YMM registers saved at trap entry.
 */
static bool emulate_unsupported_insn(struct pt_regs *regs, uint8_t (*ymm)[32])
{
	uint8_t insn[16];
	uint8_t vex_r, vex_x, vex_b, vex_m, vex_w, vex_v, vex_l, vex_pp;
	uint8_t opcode, modrm, mod, reg_op, rm;
	int dest_reg, src1_reg, src2_reg;
	int insn_len = 0;
	unsigned long ea = 0;
	uint8_t src1_val[32] __aligned(32);
	uint8_t src2_val[32] __aligned(32);
	uint8_t dest_val[32] __aligned(32);
	uint8_t imm8 = 0;
	int vector_bytes;
	int fetched;
	bool has_imm = false;
	int cur = 5;
	int i, q, j, k;

	fetched = fetch_insn(insn, regs->ip);
	if (fetched < 0)
		return false;

	/* Must start with 3-byte VEX (0xC4) */
	if (insn[0] != 0xC4) {
		pr_warn("gfni_emu: Not a 3-byte VEX prefix (0xC4) at RIP=0x%lx\n", regs->ip);
		return false;
	}

	vex_r = !(insn[1] & 0x80);
	vex_x = !(insn[1] & 0x40);
	vex_b = !(insn[1] & 0x20);
	vex_m = insn[1] & 0x1F;

	vex_w  = (insn[2] >> 7) & 1;
	vex_v  = (~(insn[2] >> 3)) & 0x0F;
	vex_l  = (insn[2] >> 2) & 1;
	vex_pp = insn[2] & 3;

	opcode = insn[3];
	modrm  = insn[4];
	mod    = (modrm >> 6) & 3;
	reg_op = (modrm >> 3) & 7;
	rm     = modrm & 7;

	dest_reg = (vex_r << 3) | reg_op;
	src1_reg = vex_v;
	src2_reg = (vex_b << 3) | rm;
	vector_bytes = vex_l ? 32 : 16;

	/* Check supported opcode classes */
	if (vex_m == 3 && vex_pp == 1) {
		/* Map 3 (0F 3A) with prefix 66 */
		if (opcode == 0xCE || opcode == 0xCF || opcode == 0x44)
			has_imm = true;
		else
			goto unsupported;
	} else if (vex_m == 2 && vex_pp == 1) {
		/* Map 2 (0F 38) with prefix 66 */
		if (opcode == 0xCF || (opcode >= 0x50 && opcode <= 0x53) ||
		    (opcode >= 0xDC && opcode <= 0xDF))
			has_imm = false;
		else
			goto unsupported;
	} else {
		goto unsupported;
	}

	/* Decode SRC2 (register or memory) */
	if (mod == 3) {
		/* Register operand */
		memcpy(src2_val, ymm[src2_reg], 32);
	} else {
		/* Memory operand */
		if (mod == 0 && rm == 5) {
			/* RIP-relative addressing */
			int32_t disp32 = *(int32_t *)(insn + cur);
			cur += 4;
			ea = (regs->ip + cur + (has_imm ? 1 : 0)) + disp32;
		} else {
			int base_reg = (vex_b << 3) | rm;
			int index_reg = 0;
			int scale = 0;
			bool has_sib = (rm == 4);

			if (has_sib) {
				uint8_t sib = insn[cur++];
				scale = (sib >> 6) & 3;
				index_reg = (vex_x << 3) | ((sib >> 3) & 7);
				base_reg = (vex_b << 3) | (sib & 7);
				if ((sib & 7) == 5 && mod == 0) {
					ea = *(int32_t *)(insn + cur);
					cur += 4;
				} else {
					ea = get_gpr(regs, base_reg);
				}
				if (index_reg != 4)
					ea += (get_gpr(regs, index_reg) << scale);
			} else {
				ea = get_gpr(regs, base_reg);
			}

			if (mod == 1) {
				int8_t disp8 = (int8_t)insn[cur++];
				ea += disp8;
			} else if (mod == 2) {
				int32_t disp32 = *(int32_t *)(insn + cur);
				cur += 4;
				ea += disp32;
			}
		}

		if (read_user_nofault(src2_val, ea, vector_bytes))
			return false;
	}

	if (has_imm)
		imm8 = insn[cur++];
	insn_len = cur;
	if (insn_len > fetched)		/* the tail of the instruction was not readable */
		return false;

	/* Read SRC1 register */
	memcpy(src1_val, ymm[src1_reg], 32);
	memset(dest_val, 0, 32);

	/* Dispatch emulation */
	if (vex_m == 3 && opcode == 0xCE) {
		/* VGF2P8AFFINEQB */
		for (q = 0; q < (vector_bytes / 8); q++) {
			uint64_t mat_qw = *(uint64_t *)(src2_val + q * 8);
			for (i = 0; i < 8; i++) {
				uint8_t v = src1_val[q * 8 + i];
				dest_val[q * 8 + i] = gf2p8_affine_byte(mat_qw, v, imm8);
			}
		}
	} else if (vex_m == 3 && opcode == 0xCF) {
		/* VGF2P8AFFINEINVQB */
		for (q = 0; q < (vector_bytes / 8); q++) {
			uint64_t mat_qw = *(uint64_t *)(src2_val + q * 8);
			for (i = 0; i < 8; i++) {
				uint8_t v = gf2p8_inv_table[src1_val[q * 8 + i]];
				dest_val[q * 8 + i] = gf2p8_affine_byte(mat_qw, v, imm8);
			}
		}
	} else if (vex_m == 3 && opcode == 0x44) {
		/* VPCLMULQDQ */
		for (q = 0; q < (vector_bytes / 16); q++) {
			int lane_offset = q * 16;
			uint64_t a = (imm8 & 0x01) ? *(uint64_t *)(src1_val + lane_offset + 8) : *(uint64_t *)(src1_val + lane_offset);
			uint64_t b = (imm8 & 0x10) ? *(uint64_t *)(src2_val + lane_offset + 8) : *(uint64_t *)(src2_val + lane_offset);
			uint64_t res_lo = 0, res_hi = 0;

			clmul64(a, b, &res_lo, &res_hi);
			*(uint64_t *)(dest_val + lane_offset) = res_lo;
			*(uint64_t *)(dest_val + lane_offset + 8) = res_hi;
		}
	} else if (vex_m == 2 && opcode == 0xCF) {
		/* VGF2P8MULB */
		for (i = 0; i < vector_bytes; i++)
			dest_val[i] = gf2p8_mul(src1_val[i], src2_val[i]);
	} else if (vex_m == 2 && (opcode >= 0xDC && opcode <= 0xDF)) {
		/* VAESENC / VAESENCLAST / VAESDEC / VAESDECLAST */
		for (q = 0; q < (vector_bytes / 16); q++) {
			int offset = q * 16;
			if (opcode == 0xDC)
				do_aesenc(dest_val + offset, src1_val + offset, src2_val + offset);
			else if (opcode == 0xDD)
				do_aesenclast(dest_val + offset, src1_val + offset, src2_val + offset);
			else if (opcode == 0xDE)
				do_aesdec(dest_val + offset, src1_val + offset, src2_val + offset);
			else if (opcode == 0xDF)
				do_aesdeclast(dest_val + offset, src1_val + offset, src2_val + offset);
		}
	} else if (vex_m == 2 && (opcode >= 0x50 && opcode <= 0x53)) {
		/* AVX-VNNI */
		memcpy(dest_val, ymm[dest_reg], 32);
		if (opcode == 0x50) {
			/* VPDPBUSD: u8(src1) * s8(src2) + dest */
			for (j = 0; j < (vector_bytes / 4); j++) {
				int32_t sum = 0;
				for (k = 0; k < 4; k++)
					sum += (int32_t)src1_val[4 * j + k] * (int32_t)(int8_t)src2_val[4 * j + k];
				*(int32_t *)(dest_val + 4 * j) += sum;
			}
		} else if (opcode == 0x51) {
			/* VPDPBUSDS: with saturation */
			for (j = 0; j < (vector_bytes / 4); j++) {
				int32_t sum = 0;
				for (k = 0; k < 4; k++)
					sum += (int32_t)src1_val[4 * j + k] * (int32_t)(int8_t)src2_val[4 * j + k];
				*(int32_t *)(dest_val + 4 * j) = sat_add32(*(int32_t *)(dest_val + 4 * j), sum);
			}
		} else if (opcode == 0x52) {
			/* VPDPWSSD: s16(src1) * s16(src2) + dest */
			for (j = 0; j < (vector_bytes / 4); j++) {
				int32_t sum = 0;
				for (k = 0; k < 2; k++)
					sum += (int32_t)*(int16_t *)(src1_val + 4 * j + 2 * k) * (int32_t)*(int16_t *)(src2_val + 4 * j + 2 * k);
				*(int32_t *)(dest_val + 4 * j) += sum;
			}
		} else if (opcode == 0x53) {
			/* VPDPWSSDS: with saturation */
			for (j = 0; j < (vector_bytes / 4); j++) {
				int32_t sum = 0;
				for (k = 0; k < 2; k++)
					sum += (int32_t)*(int16_t *)(src1_val + 4 * j + 2 * k) * (int32_t)*(int16_t *)(src2_val + 4 * j + 2 * k);
				*(int32_t *)(dest_val + 4 * j) = sat_add32(*(int32_t *)(dest_val + 4 * j), sum);
			}
		}
	}

	/* Write back to destination register */
	memcpy(ymm[dest_reg], dest_val, 32);
	if (vector_bytes == 16)
		memset(ymm[dest_reg] + 16, 0, 16);

	regs->ip += insn_len;
	return true;

unsupported:
	pr_warn("gfni_emu: Unsupported opcode: %02x %02x %02x %02x %02x %02x at RIP=0x%lx\n",
		insn[0], insn[1], insn[2], insn[3], insn[4], insn[5], regs->ip);
	return false;
}

int real_gfni_die_handler(struct notifier_block *self, unsigned long val, void *data, uint8_t (*ymm)[32]);

asm (
	".pushsection .text, \"ax\"\n"
	".p2align 4\n"
	".globl __cfi_gfni_die_handler\n"
	"__cfi_gfni_die_handler:\n"
	"    mov $0xd5127a3b, %eax\n"
	"    nop; nop; nop; nop; nop; nop; nop; nop; nop; nop; nop\n"
	".globl gfni_die_handler\n"
	"gfni_die_handler:\n"
	"    endbr64\n"
	"    pushq %rbp\n"
	"    movq %rsp, %rbp\n"
	"    andq $-32, %rsp\n"
	"    subq $512, %rsp\n"
	"    vmovdqu %ymm0, 0(%rsp)\n"
	"    vmovdqu %ymm1, 32(%rsp)\n"
	"    vmovdqu %ymm2, 64(%rsp)\n"
	"    vmovdqu %ymm3, 96(%rsp)\n"
	"    vmovdqu %ymm4, 128(%rsp)\n"
	"    vmovdqu %ymm5, 160(%rsp)\n"
	"    vmovdqu %ymm6, 192(%rsp)\n"
	"    vmovdqu %ymm7, 224(%rsp)\n"
	"    vmovdqu %ymm8, 256(%rsp)\n"
	"    vmovdqu %ymm9, 288(%rsp)\n"
	"    vmovdqu %ymm10, 320(%rsp)\n"
	"    vmovdqu %ymm11, 352(%rsp)\n"
	"    vmovdqu %ymm12, 384(%rsp)\n"
	"    vmovdqu %ymm13, 416(%rsp)\n"
	"    vmovdqu %ymm14, 448(%rsp)\n"
	"    vmovdqu %ymm15, 480(%rsp)\n"
	"    movq %rsp, %rcx\n"
	"    call real_gfni_die_handler\n"
	"    vmovdqu 0(%rsp), %ymm0\n"
	"    vmovdqu 32(%rsp), %ymm1\n"
	"    vmovdqu 64(%rsp), %ymm2\n"
	"    vmovdqu 96(%rsp), %ymm3\n"
	"    vmovdqu 128(%rsp), %ymm4\n"
	"    vmovdqu 160(%rsp), %ymm5\n"
	"    vmovdqu 192(%rsp), %ymm6\n"
	"    vmovdqu 224(%rsp), %ymm7\n"
	"    vmovdqu 256(%rsp), %ymm8\n"
	"    vmovdqu 288(%rsp), %ymm9\n"
	"    vmovdqu 320(%rsp), %ymm10\n"
	"    vmovdqu 352(%rsp), %ymm11\n"
	"    vmovdqu 384(%rsp), %ymm12\n"
	"    vmovdqu 416(%rsp), %ymm13\n"
	"    vmovdqu 448(%rsp), %ymm14\n"
	"    vmovdqu 480(%rsp), %ymm15\n"
	"    leave\n"
	"    ret\n"
	".popsection\n"
);
int gfni_die_handler(struct notifier_block *self, unsigned long val, void *data);

static unsigned long emu_count = 0;

__used noinline int real_gfni_die_handler(struct notifier_block *self, unsigned long val, void *data, uint8_t (*ymm)[32])
{
	struct die_args *args = data;
	struct pt_regs *regs = args->regs;
	unsigned long orig_ip;

	if (val != DIE_TRAP || args->trapnr != X86_TRAP_UD)
		return NOTIFY_DONE;

	if (!user_mode(regs))
		return NOTIFY_DONE;

	orig_ip = regs->ip;

	if (emulate_unsupported_insn(regs, ymm)) {
		emu_count++;
		if (emu_count <= 50 || (emu_count % 1000 == 0)) {
			pr_info("gfni_emu: [#%lu] Emulated RIP=0x%lx -> next RIP=0x%lx\n",
				emu_count, orig_ip, regs->ip);
		}
		return NOTIFY_STOP;
	}

	pr_warn("gfni_emu: Emulation failed for RIP=0x%lx\n", regs->ip);
	return NOTIFY_DONE;
}

static struct notifier_block gfni_nb = {
	.notifier_call = gfni_die_handler,
	.priority = 1000,
};

int __init real_gfni_emu_init(void);

asm (
	".pushsection .init.text, \"ax\"\n"
	".p2align 4\n"
	".globl __cfi_gfni_emu_init\n"
	"__cfi_gfni_emu_init:\n"
	"    mov $0x6fbb3035, %eax\n"
	"    nop; nop; nop; nop; nop; nop; nop; nop; nop; nop; nop\n"
	".globl init_module\n"
	"init_module:\n"
	".globl gfni_emu_init\n"
	"gfni_emu_init:\n"
	"    endbr64\n"
	"    jmp real_gfni_emu_init\n"
	".popsection\n"
);
int __init init_module(void);
int __init gfni_emu_init(void);

__used noinline int __init real_gfni_emu_init(void)
{
	int ret;
	pr_info("gfni_emu: Loading GFNI / VPCLMULQDQ / VAES / AVX-VNNI trap emulator...\n");
	init_gf2p8_tables();
	ret = register_die_notifier(&gfni_nb);
	if (ret)
		pr_err("gfni_emu: Failed to register die notifier: %d\n", ret);
	else
		pr_info("gfni_emu: Trap handler registered successfully!\n");
	return ret;
}

void __exit real_gfni_emu_exit(void);

asm (
	".pushsection .exit.text, \"ax\"\n"
	".p2align 4\n"
	".globl __cfi_gfni_emu_exit\n"
	"__cfi_gfni_emu_exit:\n"
	"    mov $0xe5c47d60, %eax\n"
	"    nop; nop; nop; nop; nop; nop; nop; nop; nop; nop; nop\n"
	".globl cleanup_module\n"
	"cleanup_module:\n"
	".globl gfni_emu_exit\n"
	"gfni_emu_exit:\n"
	"    endbr64\n"
	"    jmp real_gfni_emu_exit\n"
	".popsection\n"
);
void __exit cleanup_module(void);
void __exit gfni_emu_exit(void);

__used noinline void __exit real_gfni_emu_exit(void)
{
	unregister_die_notifier(&gfni_nb);
	pr_info("gfni_emu: Unloaded.\n");
}
