// MiniJson.h : 极简 JSON 解析器（单头文件）
// 支持本客户端所需子集：对象/数组/字符串/数字/布尔/null，
// 字符串转义含 \uXXXX（UTF-8 输出）。不支持的输入返回 false。
#pragma once

#include <cstdlib>
#include <cstring>

namespace minijson {

class Value;
using Object = std::map<std::string, Value>;
using Array = std::vector<Value>;

class Value {
public:
    enum Type { NUL, BOOLEAN, NUMBER, STRING, ARR, OBJ };

    Type type = NUL;
    bool boolean = false;
    double number = 0.0;
    std::string str;
    std::shared_ptr<Array> arr;
    std::shared_ptr<Object> obj;

    bool has(const std::string& key) const
    {
        return type == OBJ && obj && obj->find(key) != obj->end();
    }

    const Value& at(const std::string& key) const
    {
        static const Value null_value;
        if (has(key))
            return obj->at(key);
        return null_value;
    }

    std::string asStr() const { return type == STRING ? str : std::string(); }
    int asInt() const { return type == NUMBER ? static_cast<int>(number) : 0; }
};

namespace detail {

struct Parser {
    const char* p;
    const char* end;
    bool failed = false;

    explicit Parser(const std::string& text)
        : p(text.data()), end(text.data() + text.size()) {}

    void SkipWs()
    {
        while (p < end && (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n'))
            ++p;
    }

    bool ParseValue(Value& out)
    {
        SkipWs();
        if (p >= end) { failed = true; return false; }
        char c = *p;
        if (c == '{') return ParseObject(out);
        if (c == '[') return ParseArray(out);
        if (c == '"') { out.type = Value::STRING; return ParseString(out.str); }
        if (c == 't') return ParseLit("true", out, Value::BOOLEAN, true);
        if (c == 'f') return ParseLit("false", out, Value::BOOLEAN, false);
        if (c == 'n') return ParseLit("null", out, Value::NUL, false);
        out.type = Value::NUMBER;
        return ParseNumber(out.number);
    }

    bool ParseLit(const char* lit, Value& out, Value::Type t, bool b)
    {
        for (const char* q = lit; *q; ++q, ++p)
            if (p >= end || *p != *q) { failed = true; return false; }
        out.type = t;
        out.boolean = b;
        return true;
    }

    bool ParseNumber(double& out)
    {
        const char* start = p;
        if (p < end && *p == '-') ++p;
        while (p < end && ((*p >= '0' && *p <= '9') || *p == '.' || *p == 'e' ||
                           *p == 'E' || *p == '+' || *p == '-'))
            ++p;
        if (p == start) { failed = true; return false; }
        out = std::strtod(std::string(start, p).c_str(), nullptr);
        return true;
    }

    void AppendUtf8(std::string& dst, unsigned int cp)
    {
        if (cp <= 0x7f) {
            dst.push_back(static_cast<char>(cp));
        } else if (cp <= 0x7ff) {
            dst.push_back(static_cast<char>(0xc0 | (cp >> 6)));
            dst.push_back(static_cast<char>(0x80 | (cp & 0x3f)));
        } else {
            dst.push_back(static_cast<char>(0xe0 | (cp >> 12)));
            dst.push_back(static_cast<char>(0x80 | ((cp >> 6) & 0x3f)));
            dst.push_back(static_cast<char>(0x80 | (cp & 0x3f)));
        }
    }

    bool ParseHex4(unsigned int& out)
    {
        out = 0;
        for (int i = 0; i < 4; ++i) {
            if (p >= end) { failed = true; return false; }
            char c = *p++;
            out <<= 4;
            if (c >= '0' && c <= '9') out |= static_cast<unsigned>(c - '0');
            else if (c >= 'a' && c <= 'f') out |= static_cast<unsigned>(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') out |= static_cast<unsigned>(c - 'A' + 10);
            else { failed = true; return false; }
        }
        return true;
    }

    bool ParseString(std::string& out)
    {
        out.clear();
        if (p >= end || *p != '"') { failed = true; return false; }
        ++p;
        while (p < end) {
            unsigned char c = static_cast<unsigned char>(*p);
            if (c == '"') { ++p; return true; }
            if (c == '\\') {
                ++p;
                if (p >= end) { failed = true; return false; }
                char e = *p++;
                switch (e) {
                case '"': out.push_back('"'); break;
                case '\\': out.push_back('\\'); break;
                case '/': out.push_back('/'); break;
                case 'b': out.push_back('\b'); break;
                case 'f': out.push_back('\f'); break;
                case 'n': out.push_back('\n'); break;
                case 'r': out.push_back('\r'); break;
                case 't': out.push_back('\t'); break;
                case 'u': {
                    unsigned int cp = 0;
                    if (!ParseHex4(cp)) return false;
                    // 代理对
                    if (cp >= 0xd800 && cp <= 0xdbff && end - p >= 6 &&
                        p[0] == '\\' && p[1] == 'u') {
                        p += 2;
                        unsigned int lo = 0;
                        if (!ParseHex4(lo)) return false;
                        if (lo >= 0xdc00 && lo <= 0xdfff)
                            cp = 0x10000 + ((cp - 0xd800) << 10) + (lo - 0xdc00);
                        else
                            AppendUtf8(out, cp), cp = lo; // 容错：按两个码点处理
                    }
                    AppendUtf8(out, cp);
                    break;
                }
                default: failed = true; return false;
                }
            } else {
                out.push_back(static_cast<char>(c));
                ++p;
            }
        }
        failed = true;
        return false;
    }

    bool ParseArray(Value& out)
    {
        ++p; // 跳过 '['
        out.type = Value::ARR;
        out.arr = std::make_shared<Array>();
        SkipWs();
        if (p < end && *p == ']') { ++p; return true; }
        while (p < end) {
            Value item;
            if (!ParseValue(item)) return false;
            out.arr->push_back(std::move(item));
            SkipWs();
            if (p < end && *p == ',') { ++p; continue; }
            if (p < end && *p == ']') { ++p; return true; }
            break;
        }
        failed = true;
        return false;
    }

    bool ParseObject(Value& out)
    {
        ++p; // 跳过 '{'
        out.type = Value::OBJ;
        out.obj = std::make_shared<Object>();
        SkipWs();
        if (p < end && *p == '}') { ++p; return true; }
        while (p < end) {
            SkipWs();
            std::string key;
            if (!ParseString(key)) return false;
            SkipWs();
            if (p >= end || *p != ':') { failed = true; return false; }
            ++p;
            Value item;
            if (!ParseValue(item)) return false;
            (*out.obj)[std::move(key)] = std::move(item);
            SkipWs();
            if (p < end && *p == ',') { ++p; continue; }
            if (p < end && *p == '}') { ++p; return true; }
            break;
        }
        failed = true;
        return false;
    }
};

} // namespace detail

inline bool Parse(const std::string& text, Value& out)
{
    detail::Parser parser(text);
    if (!parser.ParseValue(out))
        return false;
    parser.SkipWs();
    return !parser.failed && parser.p == parser.end;
}

} // namespace minijson
