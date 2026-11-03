package com.blindnav.app

/**
 * Drops only identical short-lived ordinary speech. Safety and visual queues
 * use their own freshness/priority rules and are never silently deduplicated
 * by this helper.
 */
class SpeechDeduper(private val windowMs: Long = 1800L) {
    private var lastText = ""
    private var lastAt = Long.MIN_VALUE

    @Synchronized
    fun accept(text: String, nowMs: Long, urgent: Boolean = false): Boolean {
        if (text.isBlank()) return false
        val duplicate = !urgent && text == lastText && nowMs >= lastAt && nowMs - lastAt <= windowMs
        if (duplicate) return false
        lastText = text
        lastAt = nowMs
        return true
    }

    @Synchronized
    fun reset() {
        lastText = ""
        lastAt = Long.MIN_VALUE
    }
}
