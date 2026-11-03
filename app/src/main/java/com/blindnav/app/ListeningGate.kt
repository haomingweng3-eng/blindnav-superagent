package com.blindnav.app

/** 显式关麦撤销排队启动，自动回调不能替用户重新授权。 */
class ListeningGate {
    private var version = 0L
    private var autoAllowed = true

    @Synchronized fun manual(): Long { version++; autoAllowed = true; return version }
    @Synchronized fun once(): Long { version++; autoAllowed = false; return version }
    @Synchronized fun automatic(): Long? = if (autoAllowed) version else null
    @Synchronized fun stop(): Long { version++; autoAllowed = false; return version }
    @Synchronized fun perform(ticket: Long, action: () -> Unit): Boolean {
        if (ticket != version) return false
        action()
        return true
    }
}
