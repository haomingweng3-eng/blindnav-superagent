package com.blindnav.app

/** 只描述结果更新健康度，不把“模型正常运行”解释成“道路安全”。时间均为单调时钟。 */
class PerceptionHealth {
    enum class Channel(val label: String, val maxAgeNanos: Long) {
        FRAME("相机画面", 3_000_000_000L),
        OBSTACLE("障碍识别", 3_000_000_000L),
        GROUND("地面识别", 4_000_000_000L),
        PATH("盲道识别", 7_000_000_000L)
    }
    data class Update(val unavailable: Set<Channel>, val message: String?, val urgent: Boolean)
    private data class State(var initFailed: Boolean = false, var at: Long? = null,
        var failures: Int = 0, var goodStreak: Int = 0, var degraded: Boolean = false,
        var pendingFault: Boolean = false)
    private val states = Channel.entries.associateWith { State() }
    private var active = false
    private var generation = 0L
    private var startedAt = 0L
    private var lastNoticeAt: Long? = null
    private var initialUpdateAnnounced = false

    @Synchronized fun start(now: Long): Long {
        generation++; active = true; startedAt = now; lastNoticeAt = null
        initialUpdateAnnounced = false
        states.values.forEach { it.at = null; it.failures = 0; it.goodStreak = 0; it.degraded = false; it.pendingFault = false }
        return generation
    }
    @Synchronized fun stop() { active = false; generation++ }
    @Synchronized fun initializationFailed(channel: Channel) { states.getValue(channel).initFailed = true }
    @Synchronized fun success(channel: Channel, session: Long, sampledAt: Long) {
        if (!active || session != generation || sampledAt < startedAt) return
        val s = states.getValue(channel)
        if (s.at != null && sampledAt < s.at!!) return
        if (bad(channel, sampledAt)) {
            if (!s.degraded) s.pendingFault = true
            s.degraded = true; s.goodStreak = 0
        }
        s.at = sampledAt; s.failures = 0; s.initFailed = false
        s.goodStreak = (s.goodStreak + 1).coerceAtMost(2)
    }
    @Synchronized fun failure(channel: Channel, session: Long, permanent: Boolean = false) {
        if (!active || session != generation) return
        states.getValue(channel).let {
            it.failures = (it.failures + 1).coerceAtMost(3); it.goodStreak = 0
            if (permanent) it.initFailed = true
            if (it.failures >= 3 || permanent) {
                if (!it.degraded) it.pendingFault = true
                it.degraded = true
            }
        }
    }
    private fun bad(channel: Channel, now: Long): Boolean {
        val s = states.getValue(channel)
        return s.initFailed || s.failures >= 3 ||
            (s.at?.let { now - it !in 0L..channel.maxAgeNanos } ?: (now - startedAt >= 8_000_000_000L))
    }
    @Synchronized fun usable(channel: Channel, session: Long, now: Long): Boolean =
        active && session == generation && states.getValue(channel).at != null &&
            !states.getValue(channel).degraded && !bad(channel, now)

    @Synchronized fun poll(now: Long): Update {
        if (!active) return Update(emptySet(), null, false)
        val recovered = mutableListOf<Channel>()
        var changed = false
        for ((channel, s) in states) {
            if (s.pendingFault) { changed = true; s.pendingFault = false }
            if (bad(channel, now)) {
                if (!s.degraded) { s.degraded = true; s.goodStreak = 0; changed = true }
            } else if (s.degraded && s.goodStreak >= 2) {
                s.degraded = false; recovered.add(channel)
            }
        }
        val unavailable = states.filterValues { it.degraded }.keys.toSet()
        val repeat = unavailable.isNotEmpty() && (lastNoticeAt?.let { now - it >= 30_000_000_000L } ?: true)
        val ready = unavailable.isEmpty() && states.values.all { it.at != null }
        val message = when {
            unavailable.isNotEmpty() && (changed || repeat) ->
                unavailable.joinToString("、") { it.label } + "暂时不可用，请先在安全位置停下，用盲杖确认。可以关闭后重新打开看路"
            recovered.isNotEmpty() -> recovered.joinToString("、") { it.label } + "已恢复更新，未播报仍不代表前方安全"
            ready && !initialUpdateAnnounced -> "看路识别已开始更新，未播报不代表前方安全"
            else -> null
        }
        if (message != null) lastNoticeAt = now
        if (ready && message != null) initialUpdateAnnounced = true
        return Update(unavailable, message, unavailable.isNotEmpty())
    }
}
