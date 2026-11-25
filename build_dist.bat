@echo off
rem ============================================================
rem DGate dist package builder (single entry; merges the former
rem client\build.bat):
rem   [1/3] locate VS toolchain, build launcher Release|Win32
rem   [2/3] stage exe + default ini (UTF-16LE BOM + CRLF, same
rem         format as the launcher's own WriteDefaultIni output;
rem         BOM/CRLF hard-verified) -> zip directly to dist\client.zip
rem   [3/3] server package dist\DGateServer.zip: deploy + control
rem         scripts at zip root, server\ code subtree beside them
rem         (__pycache__ / *.pyc / runtime data dirs excluded; entries
rem         use forward slashes, Linux-safe)
rem Output: <project root>\dist\   (no intermediate client\dist)
rem ============================================================
setlocal
set "SRC=%~dp0"
for %%I in ("%~dp0..") do set "ROOT=%%~fI"
set "DIST=%ROOT%\dist"
set "STAGE=%TEMP%\DGatePkgStage"

rem ---- [1/3] locate vcvars32.bat: vswhere first, common paths fallback ----
set "VCVARS="
if exist "%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" (
    for /f "usebackq delims=" %%i in (`"%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do (
        if exist "%%i\VC\Auxiliary\Build\vcvars32.bat" set "VCVARS=%%i\VC\Auxiliary\Build\vcvars32.bat"
    )
)
if not defined VCVARS if exist "C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars32.bat" set "VCVARS=C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars32.bat"
if not defined VCVARS if exist "C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars32.bat" set "VCVARS=C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars32.bat"
if not defined VCVARS if exist "C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars32.bat" set "VCVARS=C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvars32.bat"
if not defined VCVARS if exist "C:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars32.bat" set "VCVARS=C:\Program Files\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars32.bat"
if not defined VCVARS (
    echo [ERROR] Visual Studio C++ x86 build tools not found. Install VS2022 with MFC or adjust paths.
    exit /b 1
)
call "%VCVARS%" >nul 2>&1

echo [1/3] Building DGateLauncher Release^|Win32 ...
pushd "%SRC%client"
msbuild DGateLauncher.sln /nologo /m /v:m /p:Configuration=Release /p:Platform=Win32
if errorlevel 1 (
    popd
    echo [ERROR] msbuild failed.
    exit /b 1
)
if not exist "Release\DGateLauncher.exe" (
    popd
    echo [ERROR] Release\DGateLauncher.exe not found after build.
    exit /b 1
)

echo [2/3] Staging launcher package (exe + default ini) ...
rmdir /s /q "%STAGE%" >nul 2>&1
mkdir "%STAGE%"
copy /y "Release\DGateLauncher.exe" "%STAGE%\DGateLauncher.exe" >nul
if errorlevel 1 (
    popd
    echo [ERROR] failed to stage DGateLauncher.exe.
    exit /b 1
)
set "PKG_INI=%STAGE%\DGateLauncher.ini"
set "PKG_EXAMPLE=%SRC%client\DGateLauncher.ini.example"
rem Normalize ini.example line endings to real CRLF and write UTF-16LE BOM
rem (same format as WriteDefaultIni). NOTE: -replace replacement strings do
rem NOT interpret \r\n escapes, so the newline is built from [char]13/10
rem explicitly. The same call re-reads the staged ini and hard-verifies
rem BOM + pure CRLF (no bare LF/CR, no literal "\r" text) before packaging.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$t=[IO.File]::ReadAllText($env:PKG_EXAMPLE); $nl=[string][char]13+[string][char]10; $t=$t -replace '\r?\n',$nl; [IO.File]::WriteAllText($env:PKG_INI,$t,[Text.Encoding]::Unicode); $b=[IO.File]::ReadAllBytes($env:PKG_INI); $s=[Text.Encoding]::Unicode.GetString($b,2,$b.Length-2); if(($b[0] -ne 255) -or ($b[1] -ne 254) -or ($s -match '(?<!\r)\n') -or ($s -match '\r(?!\n)') -or ($s.Contains([string][char]92+'r'))){ Write-Error 'staged ini failed BOM/CRLF check'; exit 1 }"
if errorlevel 1 (
    popd
    echo [ERROR] failed to stage a CRLF DGateLauncher.ini.
    exit /b 1
)
popd

rem remove old zip with retries: freshly written zips are often held for a
rem few seconds by AV / search indexer scans (transient lock)
if not exist "%DIST%" mkdir "%DIST%"
set /a ZIP_TRIES=0
:delclientzip
if not exist "%DIST%\client.zip" goto clientzipok
del /q "%DIST%\client.zip" >nul 2>&1
if not exist "%DIST%\client.zip" goto clientzipok
set /a ZIP_TRIES+=1
if %ZIP_TRIES% GEQ 6 (
    echo [ERROR] dist\client.zip stays locked by another process ^(AV scan or an open viewer^). Close it and retry.
    exit /b 1
)
ping -n 3 127.0.0.1 >nul
goto delclientzip
:clientzipok
"%SystemRoot%\System32\tar.exe" --format zip -cf "%DIST%\client.zip" -C "%STAGE%" DGateLauncher.exe DGateLauncher.ini
if errorlevel 1 (
    echo [ERROR] failed to create %DIST%\client.zip.
    exit /b 1
)
for %%F in ("%DIST%\client.zip") do echo   client.zip      : %%~fF  ^(%%~zF bytes^)

echo [3/3] Building server package ...
rem Stage the package layout the install scripts expect (scripts beside
rem server\), then zip with bsdtar for forward-slash entry names.
rmdir /s /q "%STAGE%" >nul 2>&1
mkdir "%STAGE%"
for %%S in (install-rhel8.sh install-centos7.sh dgate-start dgate-stop dgate-restart dgate-status) do (
    copy /y "%SRC%server\%%S" "%STAGE%\%%S" >nul
    if errorlevel 1 (
        echo [ERROR] staging %%S failed.
        exit /b 1
    )
)
rem /E include subdirs; /XD drop caches and runtime data dirs; /XF drop pyc
rem and the 6 scripts (they ship at package root, not inside server\)
robocopy "%SRC%server" "%STAGE%\server" /e /xd __pycache__ data /xf *.pyc install-rhel8.sh install-centos7.sh dgate-start dgate-stop dgate-restart dgate-status /nfl /ndl /np
if errorlevel 8 (
    echo [ERROR] staging server tree failed ^(robocopy rc=%ERRORLEVEL%^).
    exit /b 1
)
del /q "%DIST%\DGateServer.zip" >nul 2>&1
"%SystemRoot%\System32\tar.exe" --format zip -cf "%DIST%\DGateServer.zip" -C "%STAGE%" install-rhel8.sh install-centos7.sh dgate-start dgate-stop dgate-restart dgate-status server
if errorlevel 1 (
    echo [ERROR] server zip creation failed.
    exit /b 1
)
rmdir /s /q "%STAGE%" >nul 2>&1
rem Hard check: entries must use forward slashes only (Linux-safe)
"%SystemRoot%\System32\tar.exe" -tf "%DIST%\DGateServer.zip" | "%SystemRoot%\System32\find.exe" "\" >nul && (
    echo [ERROR] DGateServer.zip contains backslash entries.
    exit /b 1
)
for %%F in ("%DIST%\DGateServer.zip") do echo   DGateServer.zip : %%~fF  ^(%%~zF bytes^)
echo DONE
endlocal
