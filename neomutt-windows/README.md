# NeoMutt on Windows, without WSL

**Verdict: partial.** NeoMutt runs on Windows from PowerShell 5.1 and from Windows Terminal, and it does so without WSL. The program that runs is a normal Windows console executable, and it still needs the MSYS2 POSIX runtime (`msys-2.0.dll`). A native build with MinGW-w64, which is what the UCRT64 and MINGW64 environments are, does not succeed. NeoMutt has no Win32 port.

The practical route for Proton Mail Bridge is the MSYS2 **MSYS** environment. Install the packaged `neomutt` there, or build it with `scripts/build-msys2.sh`. Then start `neomutt.exe` from PowerShell with `C:\msys64\usr\bin` on `Path`.

## What “partial” means

| Route | Result |
| --- | --- |
| Native MinGW-w64 cross-compile from Linux (`x86_64-w64-mingw32`) | Fails. Configure can finish only after ncurses and libiconv are cross-built. The compile then stops on POSIX calls MinGW does not provide. No `neomutt.exe`. |
| MSYS2 UCRT64 or MINGW64 (native Windows CRT, no `msys-2.0.dll`) | Not a separate success. MSYS2 publishes no `mingw-w64-*-neomutt` package. Those shells use MinGW-w64, and the cross-compile above already shows that target failing. `scripts/try-ucrt64.sh` repeats the attempt on Windows. It was not run in this session. |
| MSYS2 MSYS (`msys-2.0.dll`) | Works. This is a Windows console program. The published package `neomutt` 20260616-3 was started under Wine. |
| Cygwin (`cygwin1.dll`) | Works, and NeoMutt documents it. Same idea as MSYS2: a POSIX emulation DLL. MSYS2 is the better fit here because the package is current and the steps below are scripted. |
| WSL | Works, and this note does not use it. |

“Native” in this note means a Windows PE program whose C library is UCRT or MSVCRT, with no Cygwin or MSYS DLL. NeoMutt is not that program. It is a console subsystem executable (`Subsystem 3`, PE32+) that imports `msys-2.0.dll` and then ncurses, OpenSSL, and the other libraries built for that runtime.

## Why the native build stops

NeoMutt is a Unix mail client. Its own sources (everything except the bundled Autosetup `jimsh` and the unit-test header) contain no `_WIN32` or `__MINGW32__` port. Upstream’s download page lists Cygwin and does not list Windows, MinGW, or MSYS2. A search of `neomutt/neomutt` issues turns up Cygwin reports (for example [issue 1791](https://github.com/neomutt/neomutt/issues/1791)) and no native Windows port. MSYS2’s MinGW package repository has no NeoMutt PKGBUILD. The only package is [`msys/neomutt`](https://packages.msys2.org/packages/neomutt), built from [MSYS2-packages/neomutt](https://github.com/msys2/MSYS2-packages/blob/master/neomutt/PKGBUILD).

Calls that a native Windows CRT does not provide, and that this tree uses directly:

- `fork()` and `wait` (`system.c`, `mutt/filter.c`, `send/sendmail.c`, `conn/tunnel.c`). MinGW has no `sys/wait.h` and no `fork`.
- Terminal control: `termios.h`, `tcsetattr`, `TIOCGWINSZ` (`gui/curs_lib.c`, `gui/resize.c`).
- Job-control and window signals: `SIGTSTP`, `SIGCONT`, `SIGWINCH`, `SIGCHLD`, and `sigaction` (`mutt_signal.c`, `mutt/signal.c`).
- File locking with `fcntl` and `struct flock` (`mutt/file.c`). The MSYS2 package is built with `+fcntl`.
- `pwd.h`, `grp.h`, `langinfo.h`, `locale_t` / `uselocale`, `arpa/inet.h`, `netdb.h`.
- POSIX `mkdir(path, mode)` and flags such as `O_CLOEXEC` and `O_NOFOLLOW`.

On 2026-11-25 the Linux cross-compile used commit `69f25882271a266ef5ff3d1495e4a408208425e8` and `x86_64-w64-mingw32-gcc` (GCC 13, win32 thread model). The first configure stopped with `Unable to find ncursesw library`. After a MinGW ncurses was installed it stopped with `Unable to find iconv()`. After libiconv, configure reported `Host OS: mingw32` and exited 0. `make -k` then emitted 1172 error lines (103 unique diagnostics) and left 28 `.o` files and no `neomutt.exe`. The compiler’s list is in [`evidence/mingw-cross-errors.txt`](evidence/mingw-cross-errors.txt). Reproduce it with [`scripts/build-mingw-cross.sh`](scripts/build-mingw-cross.sh). That script’s PCRE2 `regex.h` shim exists only to expose the later errors. MinGW has no POSIX `regex.h` either.

The cross compiler used here is MinGW-w64’s win32 thread model (the same family as MSYS2 MINGW64). Its headers have `unistd.h` and do not have `termios.h`, `sys/wait.h`, or `fork`. UCRT64 links the Universal CRT instead of the older MSVCRT and still uses those MinGW-w64 headers, so it does not gain `fork` or `termios`. This session did not boot a UCRT64 shell. `scripts/try-ucrt64.sh` is there so a Windows machine can record that failure directly. MSYS2 has been dropping the older MINGW64 environment in favor of UCRT64; that change does not create a native NeoMutt.

## Best route: MSYS2’s MSYS environment

Use the Start Menu entry **MSYS2 MSYS**, or `C:\msys64\usr\bin\bash.exe`. Leave the UCRT64 and MINGW64 shortcuts for other programs.

Two ways to get the executable:

1. Install the package MSYS2 already builds. This is the binary that was run under Wine.
2. Compile tag `20260616` with the same options, plus the OpenSSL 4 patch that package applies. Current MSYS2 ships OpenSSL 4.1.0. Tag `20260616` is older than that support. The patch is commit [`6a2f636`](https://github.com/neomutt/neomutt/commit/6a2f636ebed03d8d4a579ec0ff541ed6c2c90d8f).

### 1. Install MSYS2

1. Download the installer from <https://www.msys2.org/> and accept the default directory `C:\msys64`.
2. Open **MSYS2 MSYS** from the Start Menu.
3. Update the installation. The first update often asks you to close the window and open it again:

   ```bash
   pacman -Syu
   ```

   Open MSYS2 MSYS again and, if pacman still lists upgrades, run `pacman -Syu` once more.

### 2a. Install the packaged build

In the MSYS2 MSYS shell:

```bash
pacman -S neomutt
neomutt -v
```

`pacman -S neomutt` from a UCRT64 shell installs this same `msys` package. It does not build a UCRT binary. The version line is followed by a system line like:

```text
System: MSYS_NT-10.0-... x86_64
```

That `MSYS_NT` prefix means `msys-2.0.dll` is the C library. OpenSSL in the package tested here is 4.1.0, with `+openssl` and `+sasl`, which is what Proton Mail Bridge needs.

### 2b. Build from source

From PowerShell, in this directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-neomutt.ps1 -Build
```

Or, inside the MSYS2 MSYS shell, from a checkout of this folder:

```bash
bash /path/to/neomutt-windows/scripts/build-msys2.sh
```

The script refreshes the package database, installs the compilers and libraries named in the MSYS2 PKGBUILD, checks out tag `20260616`, applies the OpenSSL 4 patch when `conn/openssl.c` does not already call `ASN1_STRING_get0_data`, then configures:

```text
./configure --prefix=/usr --sysconfdir=/etc --libexecdir=/usr/lib \
  --gpgme --gss --ssl --sasl --gdbm --with-ui=ncurses \
  --with-idn2=/usr --idn=0 --idn2=1 --disable-doc
```

Documentation is left out so DocBook is not required. Set `NEOMUTT_WITH_DOCS=1` to build the manual as well. The script installs `neomutt.exe` to `/usr/bin`, which is `C:\msys64\usr\bin` on a default setup.

This from-source script was checked for shell syntax on Linux. It was not executed inside an MSYS2 shell in that session, because MSYS2 runs on Windows. The binary produced by the same recipe, package `neomutt-20260616-3`, was downloaded and started under Wine. Its `-v` output, DLL imports, and PE header are in [`evidence/msys2-wine-version.txt`](evidence/msys2-wine-version.txt).

### 3. Run it from PowerShell 5.1

Windows PowerShell 5.1 can start the exe directly. The MSYS DLL directory has to be on `Path`, and ncurses needs `TERM`. From this directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-neomutt.ps1 -v
```

`scripts\run-neomutt.ps1` prepends `C:\msys64\usr\bin`, sets `TERM` to `xterm-256color` when it is empty, sets `LANG` to `C.UTF-8` when it is empty, and runs `neomutt.exe`. Pass NeoMutt’s own arguments after the script name:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-neomutt.ps1 -F C:\msys64\home\YourWindowsName\.neomuttrc
```

The same commands work in a Windows Terminal tab whose profile is Windows PowerShell. A classic `conhost` window should switch to UTF-8 first, or non-ASCII mail will look wrong:

```powershell
chcp 65001
```

Windows Terminal’s default UTF-8 setting is the better display.

To see the Windows path of NeoMutt’s home directory:

```powershell
C:\msys64\usr\bin\cygpath.exe -w ~
```

That directory is usually `C:\msys64\home\<Windows user name>`, which is the MSYS home, not `C:\Users\<name>`. Put `.neomuttrc` there. Create the cache directory in an MSYS shell or with the runtime’s tools:

```powershell
C:\msys64\usr\bin\bash.exe -lc "mkdir -p ~/.neomutt"
```

Copy [`proton-bridge.neomuttrc`](proton-bridge.neomuttrc) to the file `cygpath -w ~/.neomuttrc` prints.

### DLLs the packaged exe loads

`objdump` of `neomutt.exe` from `neomutt-20260616-3` lists:

- `msys-2.0.dll` (package `msys2-runtime`, “Posix emulation engine for Windows”)
- `msys-ncursesw6.dll`
- `msys-ssl-4.dll`, `msys-crypto-4.dll` (OpenSSL)
- `msys-sasl2-3.dll`
- `msys-iconv-2.dll`, `msys-idn2-0.dll`, `msys-intl-8.dll`
- `msys-gdbm-6.dll`, `msys-gpgme-45.dll`, `msys-gssapi-3.dll`
- `KERNEL32.dll`

The package’s declared dependencies are `libidn2`, `libgpgme`, `libsasl`, `libgdbm`, `ncurses`, `libiconv`, `libopenssl`, `libintl`, and `heimdal-libs`. Those DLLs live in `C:\msys64\usr\bin` next to `neomutt.exe`. Do not copy the exe to another folder by itself. Further DLLs (gcc runtime, libunistring, and so on) are pulled in by those libraries; a normal `pacman -S neomutt` installs the set.

Under Wine 9.0 the same tree printed `NeoMutt 20260616` and `System: MSYS_NT-10.0-19043 3.6.12-50c44b7c.x86_64`, and `neomutt -v` exited 0. Wine also printed a Cygwin `FAST_CWD` warning. The process still exited successfully. `cygpath` translated `/usr/bin/neomutt.exe` to a `Z:\...` Windows path, and `neomutt -Q` reported `ssl_starttls = yes`, `ssl_force_tls = yes`, and `certificate_file = "~/.mutt_certificates"`.

## Proton Mail Bridge

Bridge must be running on this computer. It publishes IMAP and SMTP on the loopback address only. Proton’s defaults, which you should copy from Bridge’s Mailbox details if you have changed them:

| | Host | Port | Encryption |
| --- | --- | --- | --- |
| IMAP | `127.0.0.1` | `1143` | STARTTLS |
| SMTP | `127.0.0.1` | `1025` | STARTTLS |

The username is the address Bridge shows. The password is the mailbox password Bridge generates, not the Proton account password. [`proton-bridge.neomuttrc`](proton-bridge.neomuttrc) uses `you@example.com` and `REPLACE-WITH-BRIDGE-MAILBOX-PASSWORD`. Replace both. Deleting the `imap_pass` and `smtp_pass` lines makes NeoMutt prompt instead, which keeps the password out of the file.

The sample uses `imap://` and `smtp://` plus `ssl_starttls` and `ssl_force_tls`, which is STARTTLS. If Bridge’s advanced settings are switched to SSL, use `imaps://` and `smtps://` and the ports Bridge shows, and set `ssl_starttls` to `no`.

Bridge’s certificate is created locally. On the first connection NeoMutt asks whether to reject it, accept it once, or accept it always. Choose accept always. The sample stores that decision in `~/.neomutt/certificates`. Use the host `127.0.0.1`, which matches the certificate. The MSYS2 build includes GSSAPI, so the sample sets `imap_authenticators` and `smtp_authenticators` to `plain:login`.

Composing a message runs `notepad` through the MSYS shell. Close Notepad to hand the text back to NeoMutt. Sending uses NeoMutt’s built-in SMTP client, so a local `sendmail` is not required.

## Cygwin, if you already use it

NeoMutt’s own instructions are at <https://neomutt.org/distro/cygwin>. Install the `neomutt` package from the Cygwin setup program. The executable needs `cygwin1.dll` from `C:\cygwin64\bin`. Putting that directory on `Path` lets PowerShell start it, the same way `C:\msys64\usr\bin` does for MSYS2. Build dependencies and `./configure --ssl --sasl` are listed on that page. This investigation did not install Cygwin. The MSYS2 package is newer (Cygwin’s packaged source line is still reported as `20241002` on the NeoMutt distro page) and is the one that was executed.

## Source and license

NeoMutt is free software under the GNU General Public License, version 2 or later. The license text is [`LICENSE.md`](https://github.com/neomutt/neomutt/blob/main/LICENSE.md) in the upstream tree. Copyright is held by the authors listed in the upstream README, principally Richard Russon and the other people named there.

Source for every build described here:

- Git: <https://github.com/neomutt/neomutt>
- Commit read for the MinGW failure: `69f25882271a266ef5ff3d1495e4a408208425e8` (main, 2026-11-24; the embedded version string in that tree is still `20251211`)
- Tag the Windows build script checks out: `20260616`
- OpenSSL 4 patch the script applies when the tag lacks it: `6a2f636ebed03d8d4a579ec0ff541ed6c2c90d8f`
- MSYS2 package that was run: `neomutt-20260616-3` from <https://mirror.msys2.org/msys/x86_64/neomutt-20260616-3-x86_64.pkg.tar.zst>, built by MSYS2 CI from that tag plus the patch

This directory does not contain NeoMutt’s source, a compiled `neomutt.exe`, or third-party DLLs. The scripts download the source at build time. If you give a binary to someone else, the GPL requires that you also offer them the corresponding source, including the OpenSSL patch when you applied it.

## Files

| File | Purpose |
| --- | --- |
| `scripts/build-msys2.sh` | From-source build in the MSYS2 MSYS shell. This is the route to use. |
| `scripts/run-neomutt.ps1` | PowerShell 5.1 wrapper: `-Build` runs the script above, otherwise starts `neomutt.exe`. |
| `scripts/try-ucrt64.sh` | On-Windows native attempt for UCRT64 / MINGW64. Expected to fail. |
| `scripts/build-mingw-cross.sh` | Linux MinGW-w64 cross-compile. Expected to fail. |
| `proton-bridge.neomuttrc` | Placeholder IMAP/SMTP config for Bridge on `127.0.0.1`. |
| `evidence/mingw-cross-errors.txt` | Compiler diagnostics from the cross-compile. |
| `evidence/msys2-wine-version.txt` | PE imports and Wine `neomutt -v` transcript. |
