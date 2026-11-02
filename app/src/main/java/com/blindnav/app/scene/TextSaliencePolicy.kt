package com.blindnav.app.scene

/**
 * Always watching, not always speaking.
 *
 * OCR text is classified against the current navigation target. General screen
 * text is stored for a user query but never spoken unsolicited. This policy is
 * deliberately pure: wiring it into CameraActivity's OCR stream is still a
 * separate step, so production status remains NOT_CONNECTED.
 */
enum class TextKind { TARGET, WAYFINDING, GENERAL, NOISE }

data class TextSalienceDecision(
    val kind: TextKind,
    val speak: Boolean,
    val text: String,
    val reason: String,
)

class TextSaliencePolicy(
    target: String,
    aliases: List<String> = emptyList(),
    private val cooldownMs: Long = 10_000L,
) {
    private val targetForms = (listOf(target) + aliases)
        .map(::normalize)
        .filter { it.length >= 2 }
        .distinct()
    private val lastSpoken = HashMap<String, Long>()

    fun classify(raw: String): TextKind {
        val text = raw.trim()
        if (text.length < 2) return TextKind.NOISE
        val n = normalize(text)
        if (n.isEmpty() || n.all { it.isDigit() } || text in UI_NOISE) return TextKind.NOISE
        if (targetForms.any { n.contains(it) }) return TextKind.TARGET
        if (WAYFINDING.any { n.contains(it) }) return TextKind.WAYFINDING
        return TextKind.GENERAL
    }

    fun consider(raw: String, nowMs: Long): TextSalienceDecision {
        val kind = classify(raw)
        if (kind == TextKind.GENERAL || kind == TextKind.NOISE) {
            return TextSalienceDecision(kind, false, "", "stored_only")
        }
        val key = normalize(raw)
        val last = lastSpoken[key]
        if (last != null && nowMs >= last && nowMs - last < cooldownMs) {
            return TextSalienceDecision(kind, false, "", "text_cooldown")
        }
        lastSpoken[key] = nowMs
        val phrase = when {
            kind == TextKind.TARGET -> "发现${raw.trim()}"
            normalize(raw).contains("exit") || raw.contains("出口") -> "前方发现出口标志"
            raw.contains("入口") -> "前方发现入口标志"
            else -> "前方发现文字标志"
        }
        return TextSalienceDecision(kind, true, phrase, "salient_text")
    }

    /** User explicitly asked "上面写的什么": return text regardless of salience. */
    fun answer(raw: String): String = raw.trim()

    fun reset() = lastSpoken.clear()

    companion object {
        private val WAYFINDING = listOf("exit", "出口", "入口", "楼号", "门牌", "号口")
        private val UI_NOISE = setOf("确定", "取消", "搜索", "返回", "更多", "设置", "登录")

        private fun normalize(s: String): String =
            s.lowercase().filter { it.isLetterOrDigit() }
    }
}
