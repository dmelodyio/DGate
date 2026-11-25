// ZipExtract.h : 基于 miniz 的更新包解压（覆盖到启动器所在目录）
#pragma once

class ZipExtract {
public:
    // 将 zipPath 解压覆盖到 destDir（逐条目：建目录、同名覆盖、
    // 拒绝绝对路径与 .. 穿越）。条目为当前正在运行的 exe 时先改名
    // 自身为 <exe>.old 再写入新文件。成功返回 true；失败时 report
    // 为可展示给用户的原因，extracted 为已成功解出的文件数。
    static bool ExtractZip(const CStringW& zipPath, const CStringW& destDir,
                           CStringW& report, int& extracted);

    // 清理上次升级遗留的 <exe>.old（启动时调用）
    static void CleanupStaleOld();

private:
    // 条目名安全检查：转 \ 为 /，拒绝绝对路径/盘符/..；返回相对路径
    static bool SafeMemberName(const CStringW& name, CStringW& safeOut);
    // 条目与当前运行 exe 同名时返回 exe 全路径，否则空
    static CStringW SelfExePathIfMatch(const CStringW& destFilePath);
};
