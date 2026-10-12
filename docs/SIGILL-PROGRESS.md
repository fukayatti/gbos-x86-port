# x86_64 版 Googlebook OS が SIGILL で起動しない件: 経過メモ

作成: 2026-10-11 / 対象: `lapis-user/16452207`(Intel Panther Lake 向け、Android 17)

## 1. 結論(先に)

- このPC(AMD Ryzen 7 5700U, Zen 2)では、x86_64 版 Googlebook OS は **最初のユーザープロセス `init` が不正命令(SIGILL)で落ち、カーネルパニック**になる。
- 原因は、OS側のバイナリが **Intel の新しい世代(Panther Lake)を前提にコンパイルされている**こと。特に **GFNI**(`vgf2p8affineqb` など)と **AVX-VNNI** を、実行時判定なしで使っている。
- KVM は CPU にない命令を代わりに実行できない。QEMU TCG も GFNI 未対応(後述)。**どちらでもこのPCでは動かない。**
- 他の x86 機種(moonstone など)も同じビルド系統で、`init` は lapis とバイト単位で同一。
- 現状: 回避策は未完成。方針は「GFNI 対応CPU のPCを使う」「aarch64 に戻る」「ゲスト内で代替実行する研究」の3つ。最後の案は、ユーザーが手で進めることになった(第7節)。

## 2. 環境

| 項目 | 内容 |
|---|---|
| ホストCPU | AMD Ryzen 7 5700U (Zen 2)。`avx2` `bmi2` `sha_ni` あり。**`gfni` `vaes` `avx512*` なし** |
| ホスト | Debian 系 Linux, `/dev/kvm` 利用可, QEMU 11.x |
| イメージ | `https://dl.google.com/device/recovery/lapis-user/16452207/recovery.zip`(約7.7GB) |
| カーネル | `6.18.21-android17-5`(起動は成功) |
| 構築 | `chaitan3/gbos-vm-linux`(ローカルで `fetch_kernel_modules.sh` を1か所修正) |

## 3. 失敗の再現と原因特定

起動ログ(`logs/gb1/serial.log`)の要点:

```
Kernel panic - not syncing: Attempted to kill init! exitcode=0x00000004
CPU: 2 UID: 0 PID: 1 Comm: init
asm_exc_invalid_op
RIP: 0033:0x2e3160
Code: ... <c4> 63 b5 ce cb 00 ...
```

- `exitcode=0x4` は SIGILL。`RIP=0x2e3160` は `init`(静的リンク、非PIE)の中。
- `c4 63 b5 ce cb 00` を `objdump` で読むと **`vgf2p8affineqb ymm9,ymm9,ymm3,0x0`**。
- ホストの `/proc/cpuinfo` に `gfni` がない(`vaes`、`avx512f` もない)。
- QEMU TCG で試すと警告が出る: `TCG doesn't support requested feature: CPUID[eax=07h,ecx=00h].ECX.gfni`。つまり TCG でも逃げられない。

`init` の周辺コードは、`vpbroadcastd` → `vmovdqu` → `vpshufb` → `vgf2p8affineqb` と続く、**コンパイラが自動ベクトル化したループ**。`cpuid` で分岐する形ではなく、ビルド時のターゲットCPU設定に GFNI が含まれていると判断した。

## 4. 調査結果(数値)

### 4.1 `init`(ramdisk)

- 静的リンクの EXEC、3,137,864 バイト、sha256 先頭 `02c8c532e922d215`。
- GFNI 命令は **40個**(9か所に分散)。AVX-512 は 0。
- **lapis と moonstone で `init` は完全に同一**(サイズ・ハッシュとも一致)。カーネルのサイズ(26,723,328)も同じ。

### 4.2 イメージ全体のスキャン

範囲: `system_a`、`system_b`、`vendor_a`、`product_a`、`system_ext_a`、および 45 個の APEX(`erofs`/`ext4` を展開)。ELF x86-64 は計 2,466 個。

| 命令 | 候補数 | ファイル数 | 確認 |
|---|---|---|---|
| GFNI | 約 6,800 | 約 265 | `objdump` と**完全一致**(libyuv 197, libndk_translation 115, protobuf-lite 84, serialservice 5) |
| AVX-VNNI | 16,693 | 114 | 抽出した例を `objdump` で確認: libvpx 1,881 / libcodec2_soft_av1enc 8,130 / libcros_ml_core_internal 2,656 / **linker64 16** |
| VAES (ymm) | 3,816 | 28 | libcros_ml_core_internal と libdesktop_audio_effects_internal で各 136(`objdump`)。他は未確認 |
| AVX-IFMA | 27 | 22 | libinput で 1(`objdump`)。他は未確認 |
| CMPccXADD / SHA512・SM3・SM4 | 69 / 83 | 25 / 37 | 抽出した例では `objdump` で 0。**誤検出の可能性が高い** |

注意: APK の中に同梱されたネイティブライブラリは未スキャン(`product_a` は ELF 0 個と出るが、APK は見ていない)。数字は**下限**。

主な箇所(GFNI):

| ファイル | 命令数 |
|---|---|
| `init`(ramdisk) | 40 |
| `bionic/libc.so` | 13 |
| `linker64`(APEX 版・bootstrap 版) | 24(ほかに AVX-VNNI 16) |
| `linkerconfig` / `crash_dump64` | 32 / 12 |
| `libcrypto.so`(約10個の APEX にそれぞれ同梱) | 各 81 |
| `libicuuc.so` / `libicui18n.so` | 7 / 4 |

`libc++.so` / `libandroid_runtime.so` / `libhwui.so` は 0。全ファイルではないが、**ビルド設定に GFNI と AVX-VNNI が入っている**ことは確実。

### 4.3 リンカー経由かどうか

- 実行ファイルの `PT_INTERP`: `/system/bin/linker64` が 356 個、`/system/bin/bootstrap/linker64` が 9 個。
- 命令を含み、`PT_INTERP` を持たないのは、bootstrap の linker64、APEX の linker64、`snapuserd`(静的)。ramdisk の `init` も静的。

### 4.4 他の機種

`gbookhub.io` の開発ボード一覧では、x86(Intel)はすべて新しい世代:

- Panther Lake (Fatcat): lapis, ruby, moonstone, felino, francka, kinmen
- Wildcat Lake (Ocelot): ojal, matsu, kodkod, ocicat, hekla, coolum
- Nova Lake (Atria): penghu

Alder Lake より古い世代の機種はない。残りは Qualcomm(mica, quartz ほか)と MediaTek(sapphire)で、いずれも arm64。

- `mica` と `quartz`(同じビルド `16471258`)は arm64 の Qualcomm 機。
- URL は `https://dl.google.com/device/recovery/<codename>-user/<build>/recovery.zip`。存在すれば 200、なければ 404。
- 確認済み(HEAD): lapis / moonstone / ruby は `16452207` で 200。

## 5. 先行事例

- **OpenCore Legacy Patcher / CryptexFixup**: macOS Ventura は AVX2 を前提にしており、ユーザー空間の大部分は「AVX2 に頼りすぎていてパッチできない」とされる。対処は、**Apple がRosetta用に出している、AVX2なしの別の dyld キャッシュに差し替える**こと。命令の書き換えではない。
- **Brunch**: ChromeOS を一般の x86 PC で動かす。**CPU の世代に合うリカバリイメージを選ぶ**(第8・9世代は shyvana/bobba、第10世代は jinlon、第11世代以降は voxel)。足すのはカーネル・initramfs などで、ユーザー空間のバイナリは書き換えない。
- どちらも「CPU 要件を満たす別のビルドを使う」解き方。今回は、それに当たる**古い基準の x86 ビルドが存在しない**のが違い。

## 6. 検討した選択肢と評価

| 案 | 評価 |
|---|---|
| GFNI 対応 PC で動かす(Intel 第12世代以降、Ryzen 7000 以降) | ビルド済みの `image-security/` がそのまま使える見込み。最も確実 |
| aarch64 の VM に戻る | このPCでは TCG だが、公式の mica イメージでランチャーまで動作済み。最終目標(Raspberry Pi)に直結 |
| 個々のバイナリを書き換える | 数千か所 + 署名・検証の作り直しで、規模が大きい |
| QEMU TCG に GFNI を足す | TCG は遅く、割に合わない |
| ゲスト内での代替実行(未完成の研究) | 数日〜1週間以上の見込み。GFNI の次に AVX-VNNI も必要で、性能は未知数。第7節 |
| 他機種のイメージ | 全 x86 機種が新しい Intel 世代のため、同じ問題 |
| `DangerousAndroid/AluminiumOS` | **arm64** の GSI を `qemu-system-aarch64` で x86 ホスト上でエミュレートするもの。x86 ネイティブではない。イメージは漏洩物(README に明記) |

## 7. 現状

- ゲスト内での代替実行は、ユーザーが手で進めることになった。この作業の途中で、アシスタントの応答が安全分類器に止められた。**何が原因かは不明**。
- 代替の進め方として、aarch64 への復帰を提案済み(未回答)。

## 8. 手元に残っているもの

| パス | 内容 |
|---|---|
| `~/src/gbos-vm-linux/image-security/` | ビルド済み x86 イメージ(見かけ 34GB、実使用約 8GB) |
| `~/src/gbos-vm-linux/dl/` | 展開済みリカバリイメージ(19GB)。不要なら削除可 |
| `~/src/scan/` | 展開済みの system / vendor / APEX(約 11GB) |
| `~/src/gfni_scan.py` | GFNI 命令の数え上げ(`objdump` と一致を確認済み) |
| `~/src/isa_scan.py` | GFNI 以外の拡張命令の候補検出(VNNI などは `objdump` で追認が必要) |
| `~/src/apex_extract.py` | `.apex` / `.capex` の中身を展開 |
| `~/src/partial_extract.py` | 途中までのダウンロードから、GPT と指定パーティションだけを取り出す |
| `~/src/isa-shim/elfinfo.py` | ELF の `PT_INTERP` 分布と、命令を含む静的バイナリの一覧 |
| `~/src/isa-shim/vendor-simde/` | simde(MIT)。SIMD 命令の参照実装として取得 |

## 9. 参考リンク

- [chaitan3/gbos-vm-linux](https://github.com/chaitan3/gbos-vm-linux)
- [gbos-vm Issue #5 (Intel N97)](https://github.com/skylartaylor/gbos-vm/issues/5)
- [Every Googlebook in Development (gbookhub.io)](https://gbookhub.io/en/articles/googlebook-development-boards-models-overview)
- [Googlebooks - Chrome OS Updates (cros.tech)](https://cros.tech/googlebooks/)
- [macOS 13: Ventura (Dortania)](https://dortania.github.io/OpenCore-Install-Guide/extras/ventura.html)
- [sebanc/brunch](https://github.com/sebanc/brunch)
- [DangerousAndroid/AluminiumOS](https://github.com/DangerousAndroid/AluminiumOS)
