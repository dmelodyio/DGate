// stdafx.h : 预编译公共头（本项目未启用 PCH，仅作统一包含）
#pragma once

#define WIN32_LEAN_AND_MEAN
#define _CRT_SECURE_NO_WARNINGS
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0601   // Windows 7+
#endif

#include <afxwin.h>    // MFC 核心

#include <string>
#include <vector>
#include <map>
#include <memory>
#include <algorithm>
#include <functional>

#include "resource.h"
