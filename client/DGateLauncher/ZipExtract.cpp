// ZipExtract.cpp : 基于 miniz 的更新包解压（覆盖到启动器所在目录）
#include "stdafx.h"
#include "ZipExtract.h"
#include "Logger.h"

#include "miniz.h"

#include <shlwapi.h>
#include <shlobj.h>
#pragma comment(lib, "shlwapi.lib")
#pragma comment(lib, "shell32.lib")

namespace {

// 取当前运行 exe 的完整路径
CStringW CurrentExePath()
{
    wchar_t buf[MAX_PATH * 2] = {0};
    ::GetModuleFileNameW(nullptr, buf, ARRAYSIZE(buf));
    return CStringW(buf);
}

} // namespace

bool ZipExtract::SafeMemberName(const CStringW& name, CStringW& safeOut)
{
    CStringW n(name);
    n.Replace(L'\\', L'/');
    n.Trim();
    if (n.IsEmpty() || n.GetAt(0) == L'/')
        return false;

    // 逐段检查：拒绝 .. 与盘符（C: / C|）
    CStringW out;
    int pos = 0;
    bool first = true;
    while (pos >= 0) {
        CStringW seg = n.Tokenize(L"/", pos);
        if (seg.IsEmpty() || seg == L".")
            continue;
        if (seg == L"..")
            return false;
        if (seg.Find(L':') >= 0 || seg.Find(L'|') >= 0)
            return false;
        if (!first)
            out += L"\\";
        out += seg;
        first = false;
    }
    if (out.IsEmpty())
        return false;
    safeOut = out;
    return true;
}

CStringW ZipExtract::SelfExePathIfMatch(const CStringW& destFilePath)
{
    CStringW self = CurrentExePath();
    if (self.IsEmpty())
        return CStringW();
    // 大小写不敏感比较完整路径（destDir 即 exe 所在目录）
    if (destFilePath.CompareNoCase(self) == 0)
        return self;
    return CStringW();
}

void ZipExtract::CleanupStaleOld()
{
    CStringW self = CurrentExePath();
    if (self.IsEmpty())
        return;
    CStringW old = self + L".old";
    if (::PathFileExistsW(old)) {
        if (::DeleteFileW(old))
            Logger::Write(L"已清理升级遗留文件：" + old);
        else
            Logger::Write(L"升级遗留文件清理失败（下次启动重试）：" + old);
    }
}

bool ZipExtract::ExtractZip(const CStringW& zipPath, const CStringW& destDir,
                            CStringW& report, int& extracted)
{
    extracted = 0;

    CT2A zipUtf8(zipPath);   // miniz 使用窄字符 API（本地 ANSI 路径）
    mz_zip_archive zip;
    memset(&zip, 0, sizeof(zip));

    if (!mz_zip_reader_init_file(&zip, zipUtf8, 0)) {
        report = L"更新包无效或已损坏";
        return false;
    }

    CStringW failedName;
    bool ok = true;
    mz_uint count = mz_zip_reader_get_num_files(&zip);
    for (mz_uint i = 0; i < count && ok; ++i) {
        mz_zip_archive_file_stat st;
        if (!mz_zip_reader_file_stat(&zip, i, &st)) {
            report = L"更新包内文件信息读取失败";
            ok = false;
            break;
        }

        CStringW name = CA2W(st.m_filename, CP_UTF8);

        CStringW rel;
        if (!SafeMemberName(name, rel)) {
            report = L"更新包内含不安全路径：" + name;
            ok = false;
            break;
        }

        CStringW dest = destDir + L"\\" + rel;

        if (st.m_is_directory) {
            ::SHCreateDirectoryExW(nullptr, dest, nullptr);
            continue;
        }

        // 确保父目录存在
        int slash = dest.ReverseFind(L'\\');
        if (slash > 0) {
            CStringW parent = dest.Left(slash);
            ::SHCreateDirectoryExW(nullptr, parent, nullptr);
        }

        // 当前运行中的 exe：先改名自身，让新文件可以写入
        CStringW self = SelfExePathIfMatch(dest);
        if (!self.IsEmpty()) {
            CStringW renamed = self + L".old";
            if (::PathFileExistsW(renamed))
                ::DeleteFileW(renamed);
            if (!::MoveFileW(self, renamed)) {
                report = L"更新启动器自身失败（无法替换运行中的程序）";
                ok = false;
                break;
            }
            Logger::Write(L"升级替换启动器：已将当前程序改名 " + renamed);
        }

        // 逐条目流式解压（extract_to_callback 逐块写，避免整包驻留内存）
        if (!mz_zip_reader_extract_to_file(&zip, i, CT2A(dest), 0)) {
            failedName = name;
            report = L"解压失败：" + name;
            ok = false;
            break;
        }
        ++extracted;
    }

    mz_zip_reader_end(&zip);

    if (ok) {
        CStringW done;
        done.Format(L"更新包解压完成：%d 个文件 -> %s", extracted, destDir.GetString());
        Logger::Write(done);
    } else {
        CStringW line = L"更新包解压失败：" + report;
        Logger::Write(line);
        report = line;   // log 与弹窗共用完整描述
    }
    return ok;
}
