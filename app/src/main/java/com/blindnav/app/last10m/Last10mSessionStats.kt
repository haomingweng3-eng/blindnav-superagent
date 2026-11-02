package com.blindnav.app.last10m

/**
 * 会话运行状况的一行摘要（任务书第 5/6 节）。
 *
 * 单独建类型是为了让"面板显示什么"与"日志记什么"用同一份数，
 * 不出现面板说 runs=12、logcat 说 runs=15 这种两边各算一套的情况。
 */
data class Last10mSessionStats(
    val sessionId: String,
    val frameCount: Long,
    val ocrUs: Long,
    /** OcrGate 的原始一行：runs / dropped / throttled / timeouts / late / busy。 */
    val ocr: String,
    val speechSpoken: Long,
    val speechSuppressed: Long,
    val lastSpeechText: String,
    val lastSpeechReason: String
)
