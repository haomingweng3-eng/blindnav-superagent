package com.blindnav.app.last10m

/**
 * 一个极小的 JSON 解析器（纯 Kotlin，无依赖）。
 *
 * 为什么要自己写：Android 单元测试的类路径上 `org.json` 只是残缺桩，
 * 一调用就抛 "not mocked"。而 VLM 返回的解析恰恰是今晚最容易出错、
 * 又最需要被测试覆盖的一段（解析失败必须整体降级成 UNKNOWN，绝不能半猜半用）。
 * 所以这里把解析放进可测的纯 Kotlin 层，Android 侧只负责把字符串交进来。
 *
 * 只支持标准 JSON：object / array / string / number / true / false / null。
 * 不支持注释、NaN、重复键（后者按"后写覆盖"处理）。
 */
object MiniJson {

    class ParseException(message: String) : RuntimeException(message)

    /** 解析成一个 Value：Map<String?, Any?> / List<Any?> / String / Double / Boolean / Null 哨兵。 */
    fun parse(text: String): Any? = Parser(text).parseTopLevel()

    fun parseObject(text: String): Map<String, Any?>? = parse(text) as? Map<String, Any?>

    /** JSON 里的 `null` 与"键不存在"必须能区分，所以用一个显式哨兵。 */
    object Null

    fun isNull(v: Any?): Boolean = v == null || v === Null

    private class Parser(private val s: String) {
        private var i = 0

        fun parseTopLevel(): Any? {
            skipWs()
            val v = value()
            skipWs()
            if (i != s.length) throw ParseException("trailing characters at $i")
            return v
        }

        private fun skipWs() { while (i < s.length && s[i].isWhitespace()) i++ }

        private fun value(): Any? {
            if (i >= s.length) throw ParseException("unexpected end")
            return when (val c = s[i]) {
                '{' -> obj()
                '[' -> arr()
                '"' -> str()
                't', 'f' -> bool()
                'n' -> literal("null", Null)
                else -> if (c == '-' || c.isDigit()) number() else throw ParseException("bad char '$c' at $i")
            }
        }

        private fun obj(): Map<String, Any?> {
            val out = LinkedHashMap<String, Any?>()
            expect('{'); skipWs()
            if (peek() == '}') { i++; return out }
            while (true) {
                skipWs()
                val key = str()
                skipWs(); expect(':')
                skipWs()
                out[key] = value()
                skipWs()
                when (peek()) {
                    ',' -> { i++ }
                    '}' -> { i++; return out }
                    else -> throw ParseException("expected , or } at $i")
                }
            }
        }

        private fun arr(): List<Any?> {
            val out = ArrayList<Any?>()
            expect('['); skipWs()
            if (peek() == ']') { i++; return out }
            while (true) {
                skipWs(); out.add(value()); skipWs()
                when (peek()) {
                    ',' -> { i++ }
                    ']' -> { i++; return out }
                    else -> throw ParseException("expected , or ] at $i")
                }
            }
        }

        private fun str(): String {
            expect('"')
            val sb = StringBuilder()
            while (i < s.length) {
                val c = s[i++]
                if (c == '"') return sb.toString()
                if (c != '\\') { sb.append(c); continue }
                if (i >= s.length) throw ParseException("bad escape")
                when (val e = s[i++]) {
                    '"' -> sb.append('"')
                    '\\' -> sb.append('\\')
                    '/' -> sb.append('/')
                    'b' -> sb.append('\b')
                    'f' -> sb.append('\u000C')
                    'n' -> sb.append('\n')
                    'r' -> sb.append('\r')
                    't' -> sb.append('\t')
                    'u' -> {
                        if (i + 4 > s.length) throw ParseException("bad unicode escape")
                        sb.append(s.substring(i, i + 4).toInt(16).toChar()); i += 4
                    }
                    else -> throw ParseException("bad escape \\$e")
                }
            }
            throw ParseException("unterminated string")
        }

        private fun bool(): Boolean =
            if (s.startsWith("true", i)) { i += 4; true }
            else if (s.startsWith("false", i)) { i += 5; false }
            else throw ParseException("bad literal at $i")

        private fun literal(text: String, value: Any?): Any? {
            if (!s.startsWith(text, i)) throw ParseException("bad literal at $i")
            i += text.length
            return value
        }

        private fun number(): Double {
            val start = i
            if (peek() == '-') i++
            while (i < s.length && (s[i].isDigit() || s[i] == '.' || s[i] == 'e' || s[i] == 'E' ||
                        ((s[i] == '+' || s[i] == '-') && (s[i - 1] == 'e' || s[i - 1] == 'E')))) i++
            val raw = s.substring(start, i)
            return raw.toDoubleOrNull() ?: throw ParseException("bad number '$raw'")
        }

        private fun peek(): Char = if (i < s.length) s[i] else throw ParseException("unexpected end")

        private fun expect(c: Char) {
            if (i >= s.length || s[i] != c) throw ParseException("expected '$c' at $i")
            i++
        }
    }
}

/** 取值辅助：都遵循"拿不到就是不知道"，不猜默认值。 */
fun Map<String, Any?>?.string(key: String): String? =
    (this?.get(key) as? String)?.takeIf { it.isNotBlank() && it.uppercase() != "UNKNOWN" }

fun Map<String, Any?>?.booleanStrict(key: String): Boolean? = when (val v = this?.get(key)) {
    null, MiniJson.Null -> null
    is Boolean -> v
    is String -> when (v.lowercase()) { "true" -> true; "false" -> false; else -> null }
    is Double -> if (v == 1.0) true else if (v == 0.0) false else null
    else -> null
}

fun Map<String, Any?>?.doubleStrict(key: String): Double? = when (val v = this?.get(key)) {
    null, MiniJson.Null -> null
    is Double -> v
    is String -> v.toDoubleOrNull()
    else -> null
}

@Suppress("UNCHECKED_CAST")
fun Map<String, Any?>?.objList(key: String): List<Map<String, Any?>> =
    (this?.get(key) as? List<Any?>)?.mapNotNull { it as? Map<String, Any?> } ?: emptyList()

@Suppress("UNCHECKED_CAST")
fun Map<String, Any?>?.obj(key: String): Map<String, Any?>? = this?.get(key) as? Map<String, Any?>

/** 千分比数组 [x1,y1,x2,y2] → IntArray；结构不对就 null，绝不静默补 0。 */
fun Map<String, Any?>?.permilleBox(key: String): IntArray? {
    val raw = this?.get(key) as? List<Any?> ?: return null
    if (raw.size < 4) return null
    val values = IntArray(4)
    for (j in 0 until 4) {
        val v = raw[j]
        values[j] = when (v) {
            is Double -> v.toInt()
            is String -> v.toIntOrNull() ?: return null
            else -> return null
        }
    }
    if (values.any { it < 0 || it > 1_000 }) return null
    if (values[2] <= values[0] || values[3] <= values[1]) return null
    return values
}
