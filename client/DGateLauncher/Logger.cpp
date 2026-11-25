// Logger.cpp : UTF-8 带时间戳的追加式日志
#include "stdafx.h"
#include "Logger.h"

namespace {
CStringW g_logPath;
}

CStringW Logger::LogPath()
{
    if (!g_logPath.IsEmpty())
        return g_logPath;
    wchar_t exePath[MAX_PATH] = { 0 };
    ::GetModuleFileNameW(nullptr, exePath, MAX_PATH);
    CStringW dir(exePath);
    int pos = dir.ReverseFind(L'\\');
    if (pos >= 0)
        dir = dir.Left(pos);
    g_logPath = dir + L"\\launcher.log";
    return g_logPath;
}

void Logger::Write(const CStringW& line)
{
    FILE* f = nullptr;
    errno_t err = _wfopen_s(&f, LogPath(), L"a, ccs=UTF-8");
    if (err != 0 || !f)
        return;

    SYSTEMTIME st = { 0 };
    ::GetLocalTime(&st);
    fwprintf(f, L"[%04u-%02u-%02u %02u:%02u:%02u] %s\r\n",
             st.wYear, st.wMonth, st.wDay, st.wHour, st.wMinute, st.wSecond,
             static_cast<const wchar_t*>(line));
    fclose(f);
}

void Logger::Init()
{
    wchar_t exePath[MAX_PATH] = { 0 };
    ::GetModuleFileNameW(nullptr, exePath, MAX_PATH);
    Logger::Write(CStringW(L"===== DGate 启动器启动（Release/Debug 均写日志）====="));
    Logger::Write(CStringW(L"程序路径: ") + exePath);
    Logger::Write(L"配置文件: DGateLauncher.ini（[server] url / [inject] dlls）");
}
