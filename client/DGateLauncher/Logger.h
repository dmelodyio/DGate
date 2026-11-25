// Logger.h : 文件日志（Release 同样生效），写入 exe 同目录 launcher.log
#pragma once

class Logger {
public:
    // 程序启动记录（版本 + 路径）
    static void Init();

    // 追加一行日志（自动加本地时间戳）
    static void Write(const CStringW& line);

private:
    static CStringW LogPath();
};

