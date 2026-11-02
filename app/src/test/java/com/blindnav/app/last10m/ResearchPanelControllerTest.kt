package com.blindnav.app.last10m

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 研究面板生命周期（任务书第 7 节 / PF-09、PF-15）。
 *
 * 要防的两件事都很具体：
 *  - 先打开"看路"、再挂会话 ⇒ 面板不出现（上一版只在 onCreate 挂一次），现场读作"功能坏了"；
 *  - 卸载会话后 tick 早退、面板停在最后一次内容 ⇒ 旧的 `ARRIVAL_CONFIRMED`
 *    被读成"现在还确认到达"。对视障导航来说，把上一段的结论留在屏幕上是最糟的显示错误。
 */
class ResearchPanelControllerTest {

    @Test
    fun mountThenAttach() {
        val c = ResearchPanelController()
        assertFalse("没挂会话时不该有面板", c.attached)
        c.sessionMounted("S1")
        assertTrue("挂载会话 ⇒ 面板必须挂上", c.attached)
        assertEquals("刚挂上时显示等待帧的占位，不能显示上一段内容",
            ResearchPanelController.WAITING_TEXT, c.displayed)
    }

    @Test
    fun pauseAndResumeKeepsPanelAttachedAndDoesNotRecreate() {
        val c = ResearchPanelController()
        c.sessionMounted("S1")
        // 离开又回来（onCreate 不再触发，只有 onResume）
        val effects = c.onResume(sessionActive = true)
        assertTrue("已有面板 ⇒ 只 KEEP，不重复创建",
            effects.all { it.command == ResearchPanelController.Command.KEEP })
        assertTrue(c.attached)
        val again = c.onResume(sessionActive = true)
        assertEquals(1, again.size)
        assertEquals(ResearchPanelController.Command.KEEP, again.single().command)
    }

    @Test
    fun openCameraFirstMountSecondStillGetsPanel() {
        // 覆盖"先开看路（会话还没挂）→ 回影子屏点挂载 → 再回看路"这条真实操作顺序
        val c = ResearchPanelController()
        val before = c.onResume(sessionActive = false)
        assertFalse("没有会话 ⇒ 不该凭空造面板", c.attached)
        assertTrue(before.all { it.command == ResearchPanelController.Command.DETACH ||
            it.command == ResearchPanelController.Command.SHOW_INACTIVE })
        c.sessionMounted("S1")
        val after = c.onResume(sessionActive = true)
        assertTrue(c.attached)
        assertTrue("回到前台必须有内容可显示：$after",
            after.any { it.command == ResearchPanelController.Command.KEEP })
    }

    @Test
    fun unmountDetachesAndClearsStaleArrival() {
        val c = ResearchPanelController()
        c.sessionMounted("S1")
        c.onTick(true, "state=ARRIVAL_CONFIRMED decision=ARRIVAL_CONFIRMED")
        assertTrue("前提：面板上留着到达结论", c.displayed.contains("ARRIVAL_CONFIRMED"))
        val effects = c.sessionUnmounted()
        assertFalse("卸载 ⇒ 面板必须拆掉", c.attached)
        assertEquals("显示内容必须被清成固定占位",
            ResearchPanelController.NOT_ACTIVE, c.displayed)
        assertTrue("命令要么 DETACH 要么 SHOW_INACTIVE：$effects",
            effects.single().command == ResearchPanelController.Command.DETACH ||
                effects.single().command == ResearchPanelController.Command.SHOW_INACTIVE)
        assertFalse(c.displayed.contains("ARRIVAL_CONFIRMED"))
    }

    @Test
    fun tickAfterSessionGoneNeverKeepsOldText() {
        val c = ResearchPanelController()
        c.sessionMounted("S1")
        c.onTick(true, "ocrUs=1200 lastTexts=星巴克咖啡")
        val e = c.onTick(false, "")
        assertEquals(ResearchPanelController.Command.SHOW_INACTIVE, e.command)
        assertEquals(ResearchPanelController.NOT_ACTIVE, e.text)
        assertEquals(ResearchPanelController.NOT_ACTIVE, c.displayed)
        assertFalse("会话没了就不能再算挂着", c.attached)
    }

    @Test
    fun newSessionNeverInheritsPreviousSessionState() {
        val a = ResearchPanelController()
        a.sessionMounted("S1")
        a.onTick(true, "target=星巴克 coffee state=ARRIVAL_CONFIRMED")
        a.sessionUnmounted()
        val b = ResearchPanelController()
        b.sessionMounted("S2")
        assertFalse("新会话的面板不能带着上一段的到达结论",
            b.displayed.contains("ARRIVAL_CONFIRMED"))
        assertFalse(b.holdsStaleContentOf("S1"))
        assertTrue("新会话只知道自己 id", b.holdsStaleContentOf("S2"))
        assertEquals("S2", b.sessionId)
    }

    @Test
    fun replacingSessionWhileMountedRefreshesInsteadOfStacking() {
        // 用户在影子屏改了 POI 再点一次挂载：会话换了，面板只能换内容，不能叠两层
        val c = ResearchPanelController()
        c.sessionMounted("S1")
        c.onTick(true, "target=星巴克咖啡")
        val effects = c.sessionMounted("S2")
        assertTrue("换会话 ⇒ 重建面板（ATTACH），不是叠加",
            effects.any { it.command == ResearchPanelController.Command.ATTACH })
        assertEquals("S2", c.sessionId)
        assertFalse("旧的 OCR/目标内容必须被盖掉", c.displayed.contains("星巴克咖啡"))
    }
}
