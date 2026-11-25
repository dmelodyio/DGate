// GameLauncher.cpp : CreateRemoteThread + LoadLibraryW 经典注入（按配置清单）
#include "stdafx.h"
#include "GameLauncher.h"
#include "Logger.h"

#include <shlwapi.h>
#pragma comment(lib, "shlwapi.lib")

CStringW GameLauncher::ExeDir()
{
    wchar_t path[MAX_PATH] = { 0 };
    ::GetModuleFileNameW(nullptr, path, MAX_PATH);
    CStringW dir(path);
    int pos = dir.ReverseFind(L'\\');
    if (pos >= 0)
        dir = dir.Left(pos);
    return dir;
}

bool GameLauncher::InjectDll(HANDLE hProcess, const CStringW& dllPath, CString& error)
{
    const SIZE_T bytes = static_cast<SIZE_T>((dllPath.GetLength() + 1) * sizeof(wchar_t));

    LPVOID remoteMem = ::VirtualAllocEx(hProcess, nullptr, bytes,
                                        MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
    if (!remoteMem) {
        error.Format(L"VirtualAllocEx 失败（错误码 %u）", ::GetLastError());
        return false;
    }

    bool ok = false;
    do {
        if (!::WriteProcessMemory(hProcess, remoteMem, dllPath.GetString(), bytes, nullptr)) {
            error.Format(L"WriteProcessMemory 失败（错误码 %u）", ::GetLastError());
            break;
        }

        auto loadLibraryW = reinterpret_cast<LPTHREAD_START_ROUTINE>(
            ::GetProcAddress(::GetModuleHandleW(L"kernel32.dll"), "LoadLibraryW"));
        if (!loadLibraryW) {
            error = L"无法定位 LoadLibraryW";
            break;
        }

        HANDLE hThread = ::CreateRemoteThread(hProcess, nullptr, 0, loadLibraryW,
                                              remoteMem, 0, nullptr);
        if (!hThread) {
            error.Format(L"CreateRemoteThread 失败（错误码 %u）", ::GetLastError());
            break;
        }

        if (::WaitForSingleObject(hThread, 15000) != WAIT_OBJECT_0) {
            error = L"注入线程超时";
            ::TerminateThread(hThread, 0);
            ::CloseHandle(hThread);
            break;
        }

        DWORD exitCode = 0;
        ::GetExitCodeThread(hThread, &exitCode);
        ::CloseHandle(hThread);
        if (exitCode == 0) {
            error = L"LoadLibraryW 在目标进程内返回空（DLL 加载失败）";
            break;
        }
        ok = true;
    } while (false);

    ::VirtualFreeEx(hProcess, remoteMem, 0, MEM_RELEASE);
    return ok;
}

bool GameLauncher::LaunchAndInject(const CStringW& token,
                                   const std::vector<CStringW>& dllList,
                                   CString& report)
{
    const CStringW dir = ExeDir();
    const CStringW gameExe = dir + L"\\dnf.exe";

    if (!::PathFileExistsW(gameExe)) {
        report = L"未找到 dnf.exe，请将启动器放在游戏目录下再运行。";
        Logger::Write(L"拉起游戏失败: 未找到 dnf.exe（目录 " + dir + L"）");
        return false;
    }

    // 带参数启动 dnf.exe（参数 = 登录签名字符串）
    CStringW cmdLine = L"\"" + gameExe + L"\" " + token;
    Logger::Write(L"拉起游戏命令行: " + cmdLine);
    std::vector<wchar_t> cmdBuf(cmdLine.GetString(),
                                cmdLine.GetString() + cmdLine.GetLength() + 1);
    STARTUPINFOW si = { sizeof(STARTUPINFOW) };
    PROCESS_INFORMATION pi = { 0 };
    if (!::CreateProcessW(gameExe, cmdBuf.data(), nullptr, nullptr, FALSE,
                          0, nullptr, dir, &si, &pi)) {
        report.Format(L"启动 dnf.exe 失败（错误码 %u）", ::GetLastError());
        Logger::Write(report);
        return false;
    }
    {
        CStringW pidText;
        pidText.Format(L"游戏进程已创建: PID %u", pi.dwProcessId);
        Logger::Write(pidText);
    }

    // 等待游戏进程完成基本初始化
    ::WaitForInputIdle(pi.hProcess, 5000);
    ::Sleep(1500);

    // 清单条目 -> 绝对路径（相对路径基于启动器所在目录）
    std::vector<CStringW> dlls;
    dlls.reserve(dllList.size());
    for (const auto& item : dllList) {
        if (item.GetLength() >= 2 && item[1] == L':')
            dlls.push_back(item);
        else
            dlls.push_back(dir + L"\\" + item);
    }

    if (dlls.empty()) {
        Logger::Write(L"注入清单为空，已直接进入游戏");
        ::CloseHandle(pi.hThread);
        ::CloseHandle(pi.hProcess);
        report.Empty();
        return true;
    }

    int injected = 0;
    CStringW failedList;

    for (const auto& dll : dlls) {
        int slash = dll.ReverseFind(L'\\');
        const CStringW name = dll.Mid(slash + 1);
        if (!::PathFileExistsW(dll)) {
            failedList += name + L"：文件不存在（" + dll + L"）\r\n";
            Logger::Write(L"插件注入失败: " + dll + L"（文件不存在）");
            continue;
        }
        CString error;
        if (InjectDll(pi.hProcess, dll, error)) {
            ++injected;
            Logger::Write(L"插件注入成功: " + dll);
        } else {
            failedList += name + L"：" + error + L"\r\n";
            Logger::Write(L"插件注入失败: " + dll + L"（" + error + L"）");
        }
    }

    ::CloseHandle(pi.hThread);
    ::CloseHandle(pi.hProcess);

    // 汇总：全部成功时静默（仅写日志，不弹窗）；存在失败时 report 非空，
    // 由调用方弹窗提示。
    CStringW summary;
    summary.Format(L"注入完成: %d / %d", injected, static_cast<int>(dlls.size()));
    if (failedList.IsEmpty()) {
        Logger::Write(summary + L"，全部成功（不弹窗）");
        report.Empty();
    } else {
        report.Format(L"游戏已启动（PID %u），%d / %d 个插件注入成功。\r\n\r\n"
                      L"以下插件注入失败：\r\n%s",
                      pi.dwProcessId, injected, static_cast<int>(dlls.size()),
                      failedList.GetString());
        Logger::Write(report);
    }
    return true;
}
