package com.blindnav.app.last10m

/**
 * 文字归一化与模糊匹配（纯 Kotlin，不依赖 Android）。
 *
 * 为什么单独成文件：OCR 出来的店名几乎不会是 POI 名的原样。真实差异来自
 * 全/半角、空格、间隔号、大小写、"星巴克咖啡" vs "星巴克" vs "STARBUCKS COFFEE"、
 * 以及 OCR 自己把字认错。只做 `==` 相等比较的系统在门口一定认不出目标。
 *
 * 但**模糊匹配是有代价的方向**：把隔壁店认成目标（Neighbor Door False Acceptance 的前半段）
 * 比认不出来危险得多。所以这里的原则是：
 *  - 匹配强度分档返回，由调用方决定哪一档够格锁定目标；
 *  - 短串（1–2 字）不允许走模糊档，避免"门"这种片段到处命中；
 *  - 每个结果都带 `matchedAlias` 与 `kind`，可回溯"当初凭什么说它是目标"。
 */
object TextNormalizer {

    /** 中文店名里常见、但不影响身份判断的后缀/连接词。 */
    val NOISE_SUFFIXES = listOf(
        "咖啡", "咖啡店", "咖啡厅", "餐厅", "饭店", "超市", "便利店", "商店", "门店", "分店",
        "专卖店", "旗舰店", "体验店", "大药房", "药店", "银行", "营业厅", "服务中心",
        "coffee", "coffee", "cafe", "store", "shop", "branch"
    )

    /** 通用业态词：它们描述的是"哪一类店"，不是"哪一家店"。 */
    val GENERIC_FRAGMENTS = setOf(
        "银行", "超市", "便利", "中心", "服务", "酒店", "餐厅", "咖啡", "大厦", "商场",
        "药店", "门店", "分店", "总店", "商店", "营业厅", "服务中心", "专卖店", "旗舰店",
        "体验店", "大药房", "饭店", "咖啡厅", "便利店", "store", "shop", "cafe", "bank"
    )

    private val PUNCT = charArrayOf(
        ' ', '\t', '　', '.', '·', '・', '，', ',', '、', '。', '-', '_', '—', '–',
        '(', ')', '（', '）', '[', ']', '【', '】', '\'', '"', '“', '”', '‘', '’', '/', '\\', '！', '!', '？', '?'
    )

    /** 全角转半角 + 去标点空格 + 小写。不做繁简转换（当前数据里没有繁体来源）。 */
    fun normalize(input: String): String {
        val sb = StringBuilder(input.length)
        for (ch in input) {
            if (ch in PUNCT) continue
            val mapped = when (ch) {
                in '！'..'～' -> (ch - 0xFEE0)          // 全角 ASCII → 半角
                in '０'..'９' -> (ch - 0xFEE0)
                else -> ch
            }
            sb.append(mapped.lowercaseChar())
        }
        return sb.toString()
    }

    /** 去掉常见后缀；只在去掉后仍非空时才去，避免把整串吃光。 */
    fun stripNoise(normalized: String): String {
        var out = normalized
        var changed = true
        while (changed && out.isNotEmpty()) {
            changed = false
            for (suffix in NOISE_SUFFIXES) {
                if (out.length > suffix.length && out.endsWith(suffix)) {
                    out = out.removeSuffix(suffix); changed = true
                }
            }
        }
        return out
    }

    /** 编辑距离。串很短（店名一般 < 20 字符），不需要优化。 */
    fun levenshtein(a: String, b: String): Int {
        if (a == b) return 0
        if (a.isEmpty()) return b.length
        if (b.isEmpty()) return a.length
        var previous = IntArray(b.length + 1) { it }
        var current = IntArray(b.length + 1)
        for (i in 1..a.length) {
            current[0] = i
            for (j in 1..b.length) {
                val cost = if (a[i - 1] == b[j - 1]) 0 else 1
                current[j] = minOf(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + cost)
            }
            val swap = previous; previous = current; current = swap
        }
        return previous[b.length]
    }

    fun similarity(a: String, b: String): Double {
        if (a.isEmpty() && b.isEmpty()) return 1.0
        val longest = maxOf(a.length, b.length)
        if (longest == 0) return 0.0
        return 1.0 - levenshtein(a, b).toDouble() / longest
    }

    /** 允许走模糊档的最小串长。短串模糊匹配等于到处乱认。 */
    const val FUZZY_MIN_LENGTH = 3

    /** 模糊档相似度门限。宁可认不出，不可认错。 */
    const val FUZZY_THRESHOLD = 0.75
}
