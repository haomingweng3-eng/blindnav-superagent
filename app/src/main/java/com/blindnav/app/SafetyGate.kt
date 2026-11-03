package com.blindnav.app

/** 原生安全命令不依赖 WebView 或网络；页面清理完成前丢弃旧请求的副作用。 */
class SafetyGate {
    private var version = 0L
    private var waitingForPage = false

    @Synchronized fun token(): Long = version
    @Synchronized fun beginSafetyCommand(): Long {
        waitingForPage = true
        return ++version
    }
    @Synchronized fun acknowledge(token: Long): Boolean {
        if (token != version) return false
        waitingForPage = false
        return true
    }
    @Synchronized fun effectsAllowed(): Boolean = !waitingForPage
    @Synchronized fun responseAllowed(token: Long): Boolean = token == version && !waitingForPage
    @Synchronized fun perform(token: Long? = null, effect: () -> Unit): Boolean {
        if (waitingForPage || (token != null && token != version)) return false
        effect()
        return true
    }

    companion object {
        fun classify(text: String): String? {
            val t = text.trim().replace(Regex("[，,。！!\\s]"), "").removePrefix("小导")
            if (Regex("^(停止找门|停止找入口|取消找门|取消找入口|不找门了|不找入口了|别找门了|别找入口了|别找了)[啊吧]*$").matches(t)) return "door"
            if (Regex("^(关相机|关闭相机|别看了|停止看路|关掉看路)$").matches(t)) return "camera"
            if (Regex("^(停|停下|停一下|停止|停止导航|结束导航|结束行程|取消|取消导航|取消行程|关掉导航|关闭导航|不去了|不走了|不想去了|不要了|不用了|算了|别说了)[啊吧了]*$").matches(t)) return "stop"
            if (Regex("^(救命|求助|帮帮我|我不舒服|我摔倒了|摔倒了|需要帮助)").containsMatchIn(t)) return "help"
            return null
        }
    }
}

object NativeSafety {
    val gate = SafetyGate()
    // 关闭相机或取消找门只取消视觉/页面请求；只有结束行程才失效路线授权。
    val navigation = SafetyGate()
}
