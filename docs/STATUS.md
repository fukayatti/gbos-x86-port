# x86_64 版 Googlebook OS (lapis) をこの PC の KVM で動かす: 状況メモ

更新: 2026-10-12 / 前提: ホストは Lenovo 82R9 (Ryzen 7 5700U / Zen 2, Radeon Lucienne, UEFI)

## 1. 現在の到達点

- x86_64 イメージ (`lapis-user/16452207`) が KVM で起動し、**セットアップウィザードの画面まで表示**される。
  `system_server` は Phase 1000 (BOOT_COMPLETED) に到達、SurfaceFlinger はホストの Radeon を virgl 経由で使う
  (`renderer: virgl (AMD Radeon Graphics (radeonsi, renoir ...))`)。
- 残っているクラッシュは Bluetooth と、Trusty 前提の HAL (secretkeeper など)。既知で、今は放置している。
- 起動に必要だった対処は次の 6 つ。

| # | 症状 | 原因 | 対処 | 場所 |
|---|---|---|---|---|
| 1 | `init` が 1.1 秒で SIGILL → panic | OS が GFNI / AVX-VNNI を前提にビルドされ、Zen 2 に命令がない | カーネルモジュールで #UD を捕まえて代替実行 | `work/gfni_module/gfni_emu.c` |
| 2 | モジュールの `affine` 計算が不正 | 行列の向きが SDM と転置になっていた | SDM 定義に修正 (simde で 10 万回照合して一致) | 同上 |
| 3 | `zygote` が無言で `abort()` | native bridge (`libhoudini.so`) | `ro.dalvik.vm.native.bridge=0` | `apply_system_update.py` (`GBOS_NATIVE_BRIDGE=off`) |
| 4 | `system_server` が `UnsatisfiedLinkError` | 過去の BISECT スタブ (`libperfetto_framework_jni.so`) が残存 | 元のファイルを復元 | `apply_system_update.py` |
| 5 | セットアップが「充電が必要」で進まない / 画面が数分で消える | EC (`/dev/cros_ec`) がなく電池 0%、スリープ | 起動完了時に電池を AC / 100% に偽装し、`svc power stayon true` | `apply_vendor_update.py` (`etc/init/vm-fake-battery.rc`) |
| 6 | 操作が 5 秒に 1 回しか効かない | `libz.so` の Adler-32 が AVX-VNNI (`vpdpwssd`) で、毎秒約 33 万回 #UD | 同じソースを CPU 基準を下げて再ビルドした `libz.so` に差し替え | `~/src/zlib-build/`、`apply_*_update.py` |

- 6 の効果 (計測済み): 定常状態の #UD は約 33 万回/秒 → 1 万回/秒未満。初回のパッケージスキャン中
  (起動 130〜310 秒) だけ約 10 万回/秒の山が残る。熱い場所は `libcrypto.so` (BoringSSL) とみられ、初回だけなので放置。
- `/data` の保持: 同じ構成で 2 回続けて起動すれば保持できる (1 回目 約 340 秒、2 回目 約 90 秒、暗号化エラーなし)。
  qcow2 の差分レイヤーを使う起動スクリプトは `~/src/isa-shim/boot_vm_overlay.sh`
  (`GBOS_OVERLAY=~/src/isa-shim/overlay/persist.qcow2`)。ベースイメージは汚れない。
  起動構成 (initrd の fstab、カーネル引数など) を途中で変えると、鍵が合わなくなり recovery に再起動する。

## 2. 起動・検証の手順

```sh
# 実験は必ずスナップショットで (イメージの /data を汚さない)
cd ~/src/gbos-vm-linux
GBOS_SNAPSHOT=on ~/gbos-vm/boot_vm.sh "$PWD" <名前> <秒数> \
  "cfi=norand loglevel=4 androidboot.selinux=permissive enforcing=0 androidboot.vendor.boot_patchlevel=2026-09-01 androidboot.boot_patchlevel=2026-09-01 androidboot.dalvik.vm.enable_uffd_gc=true androidboot.zygote.disable_gl_preload"
```

- `cfi=norand` は必須 (モジュールの型ハッシュが手書きのため)。
- 通常起動 (非スナップショット) の 2 回目は、`/data` の暗号化ポリシー不一致で recovery に再起動する。
  やむを得ず通常起動するときは、毎回 userdata の先頭 1 MiB (オフセット 19070451712) を 0 で潰す。
- スナップショットの一時領域はディスクに作られる (1 回で約 5.5 GB)。空きが 874 MB のときは `Buffer I/O error` になった。
- 書き換えスクリプト (`apply_system_update.py` / `apply_vendor_update.py`) はイメージを直接書き換えるので、
  **VM が動いていないときに実行する**。`apply_system_update.py` は `~/src/scan/system_a` を読むので消さない。
- 重ね書き方式のため、適用のたびに erofs が約 10〜40 MB 増える。

## 3. 計測のしかた (性能の調査用)

- `loglevel=4` だとモジュールの `pr_info` が隠れる。計測するときだけ `loglevel` を外して (既定の 7)、
  `gfni_emu: [#N] Emulated RIP=...` の行 (1000 回ごと) から、1 秒あたりの回数と RIP の下位 12 ビットの偏りを見る。
- 過去の実行 (`test_ui31`): 起動 84 秒で累計 2,765 万回 = 約 33 万回/秒。RIP の偏りは `libz.so` の `vpdpwssd` 8 か所と一致した。
- 命令の位置から場所を特定する道具: `~/src/isa_scan.py` (候補の検出)、`~/src/gfni_scan.py`、`~/src/apex_extract.py`、
  `~/src/isa-shim/elfinfo.py`。展開済みのシステムは `~/src/scan/` にある。

## 4. 次にやること (VM 側)

1. libz 差し替えの効果を、`loglevel` を外して、1 秒あたりの回数で確認する。残りが多ければ、次の熱い場所を探す
   (APEX 内の静的な zlib、`libcrypto` など)。
2. セットアップウィザードを最後まで通す。ネットワーク (QEMU の user モード NAT)、入力 (マウス / キーボード)、日本語入力。
3. `/data` の 2 回目起動の問題を直す (暗号化ポリシーの不一致)。
4. 起動時間の短縮 (初回の最適化処理を、イメージ側で済ませておく)。

## 5. 実機 (Brunch 化) に向けた事実

ホスト: Lenovo 82R9 / Ryzen 7 5700U / Radeon Lucienne / Realtek RTL8822CE / KIOXIA NVMe / ELAN06FA タッチパッド。

Googlebook カーネル (6.18.21-android17-5) の config (イメージに埋め込み。`/tmp` に抽出済みだった) との突き合わせ:

| 部品 | 状況 |
|---|---|
| NVMe, xHCI, USB ストレージ / UAS, HID multitouch | 組み込み (`=y`) で、そのまま使える |
| GPU | **`DRM_AMDGPU` なし。`DRM_SIMPLEDRM` / `SYSFB_SIMPLEFB` も無効** → 画面が出ない |
| タッチパッド | `I2C_HID_ACPI` と `PINCTRL_AMD` が無効 |
| Wi-Fi | `rtw88` なし |
| 音声 | `SND_HDA_INTEL` なし |
| 電池 / EC | `cros_ec` 系のみ (Intel Chromebook 向け) |
| CPU の命令 | GFNI/VNNI は、今のモジュールで代替実行できる (実機でも同じ) |

- 結論: 実機には、**カーネルの自前ビルドが必須**。`android17-6.18` のソース (AOSP `kernel/common`) を、
  イメージの config をそのまま使い、上の不足分を **モジュール (=m)** で足す方針が、既存のモジュールとの互換 (modversions) を保ちやすい。
- 必要になる容量の目安: ソース + ビルドで 20〜30 GB。現状の空きは約 4 GB なので、先に整理が必要 (ARM64 用の展開イメージ約 18 GB など)。
- 実機ではシリアルがないので、デバッグ手段 (netconsole / pstore) を最初に決める。
- 方針 (ユーザー): KVM で十分に試して、まともに動くようになってから、容量を整理して実機化する。

## 6. 手元の主な場所

| パス | 内容 |
|---|---|
| `~/src/gbos-vm-linux/image-security/googlebook-security.bin` | 現在のイメージ (実使用は 10 GB 前後) |
| `~/gbos-vm/work/apply_system_update.py`, `apply_vendor_update.py` | イメージの書き換え (環境変数で切り替え) |
| `~/gbos-vm/work/gfni_module/` | 命令エミュレーション用モジュール |
| `~/src/zlib-build/` | 基準を下げた `libz.so` のビルド (`build.sh`、`out/libz.so`) |
| `~/src/scan/` | 展開済みの元システム (消さない) |
| `~/src/isa-shim/SIGILL-PROGRESS.md` | 初期の経過 (SIGILL の調査) |

## 7. 追記 (2026-10-13): 実機 (段階2) の入り口

x86_64 VM は、次の点まで実用的に動くようになった (詳細は各節と、会話のメモ)。

- UI (デスクトップ、Chrome のページ描画、設定アプリ) が動く。Chrome は Vulkan を隠して GLES (virgl) で描画。
- `/data` は qcow2 の差分レイヤーで保持できる。ホストを守る起動: `~/src/isa-shim/run_vm.sh` (メモリ 4GB、cgroup 制限、ロガー)。
- ポインター: usb-tablet + ゲスト内ブリッジ `vm-pointer-bridge` (クリックは正確、どの縦横比でも可。ホイールの最終版は要確認)。
- 画面解像度は 4:3 の 1440x1080 を既定にした (ブリッジなしの Wacom 経路を使うときの制約)。

実機に向けた事実:

- 出荷カーネルの正体: `6.18.21-android17-5-g3938c8d97142-ab16302624` = AOSP `kernel/common` のタグ
  `android17-6.18-2026-06_r52` (コミット `3938c8d97142d5cf31543aa96f864a2cf832dda0`)。ソースは公開されている。
- 出荷カーネルの config は、イメージに埋め込まれている (`lapis_kernel_config.txt` に抽出済み)。
- **amdgpu は、出荷カーネルに後から足せない**とみる (GKI はエクスポートするシンボルを絞る設定のため)。
  設定を変えて、カーネルごと作り直す。足すもの: `amdgpu` と依存 (`DRM_BUDDY` `DRM_EXEC` `DRM_SCHED`
  `DRM_DISPLAY_HELPER` ほか)、`I2C_HID_ACPI`、`PINCTRL_AMD`、`rtw88` (RTL8822CE)、`SND_HDA_INTEL`、
  `ACPI_BATTERY` / `ACPI_AC`、`AMD_PMC`、`CFG80211`、`BT_HCIBTUSB`。
- ホスト (Lenovo 82R9) のツールは揃っている: clang 21.1.8、lld、make、bc、flex、bison、pahole、openssl。
  Google 製の clang 22 とは別なので、再現ビルドで差が出ないかを最初に確認する。
- 手順 (段階2): (1) ソース取得 `~/src/kernel/common` (2) 同じ config で再現ビルド (3) 設定変更して、
  上のドライバをモジュールで追加し、既存の vendor モジュール (xe など) が読めるか確認 (4) USB-SSD への組み立て
  (GRUB、initrd、`androidboot.boot_devices` の修正)、デバッグ手段 (netconsole / pstore) の確保。
- 実機は、内蔵 NVMe には触れず、**外付け USB-SSD から起動**する前提。Secure Boot は BIOS で無効にする。
