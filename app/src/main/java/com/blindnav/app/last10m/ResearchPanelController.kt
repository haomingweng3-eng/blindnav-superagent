package com.blindnav.app.last10m

/**
 * 研究面板的生命周期（任务书第 7 节 / PF-09 + PF-15）。
 *
 * 上一版的两个坑都很具体：
 *  1. 面板只在 `onCreate` 里挂载。于是"先打开看路、再回影子屏点挂载"这条路
 *     走完，屏幕上一个提示都没有——现场只会认为功能坏了。
 *  2. 卸载会话后 tick 直接早退，面板停在最后一次内容上。旧的那行
 *     `ARRIVAL_CONFIRMED` 会被读成"现在仍然确认到达"。
 *
 * 这里把"该挂、该留、该拆、该显示什么"从 View 代码里抽出来，
 * 成为一台可以在电脑上逐条断言的小状态机；CameraActivity 只负责执行命令。
 * 判定层（状态机/到达授权）完全不经过这里，所以这层不会改变任何生产结论。
 */
class ResearchPanelController {

    enum class Command {
        /** 创建并挂载面板 */
        ATTACH,
        /** 已挂载，不要重复创建 */
        KEEP,
        /** 拆除面板 */
        DETACH,
        /** 刷新文字 */
        RENDER,
        /** 没有会话：清空旧内容，显示固定占位 */
        SHOW_INACTIVE
    }

    data class Effect(val command: Command, val text: String = "")

    var attached: Boolean = false
        private set
    var displayed: String = ""
        private set
    var sessionId: String? = null
        private set

    /** 会话挂载（或换了一个会话）。换会话必须先把旧内容清掉，不能带过来。 */
    fun sessionMounted(newSessionId: String): List<Effect> {
        val replaced = sessionId != null && sessionId != newSessionId
        sessionId = newSessionId
        return if (!attached || replaced) {
            attached = true
            displayed = WAITING_TEXT
            listOf(Effect(if (replaced) Command.ATTACH else Command.ATTACH, WAITING_TEXT))
        } else {
            displayed = WAITING_TEXT
            listOf(Effect(Command.RENDER, WAITING_TEXT))
        }
    }

    /** 会话卸载：面板必须拆掉或至少清空，绝不保留旧的 ARRIVAL/OCR 结果。 */
    fun sessionUnmounted(): List<Effect> {
        sessionId = null
        val had = attached
        attached = false
        displayed = NOT_ACTIVE
        return listOf(Effect(if (had) Command.DETACH else Command.SHOW_INACTIVE, NOT_ACTIVE))
    }

    /**
     * onResume。这里覆盖"先开相机再挂载"这条路：
     * 有会话而面板不在 ⇒ 挂上；面板在 ⇒ 只 KEEP，不重复创建。
     */
    fun onResume(sessionActive: Boolean): List<Effect> = when {
        !sessionActive -> sessionUnmounted()
        !attached -> {
            attached = true
            displayed = WAITING_TEXT
            listOf(Effect(Command.ATTACH, WAITING_TEXT))
        }
        else -> listOf(Effect(Command.KEEP))
    }

    /** 每秒 tick。没有会话就显示占位，有会话就刷成新内容。 */
    fun onTick(sessionActive: Boolean, statusText: String): Effect {
        if (!sessionActive) {
            if (attached) { attached = false; sessionId = null }
            displayed = NOT_ACTIVE
            return Effect(Command.SHOW_INACTIVE, NOT_ACTIVE)
        }
        if (!attached) {
            attached = true
            displayed = statusText
            return Effect(Command.ATTACH, statusText)
        }
        displayed = statusText
        return Effect(Command.RENDER, statusText)
    }

    /** 面板上还留着上一段会话的内容吗？测试与现场都要能一句话回答。 */
    fun holdsStaleContentOf(otherSessionId: String?): Boolean =
        otherSessionId != null && sessionId == otherSessionId && attached

    fun reset() { attached = false; displayed = ""; sessionId = null }

    companion object {
        /** 会话还在、但还没有一帧可用时的占位。不写"等待中"这种没信息量的话。 */
        const val WAITING_TEXT = "LAST10M 会话已挂载，等待相机帧…"
        /** 会话不在时的固定显示。旧的到达结论必须被它盖掉。 */
        const val NOT_ACTIVE = "LAST10M_SESSION_NOT_ACTIVE"
    }
}
