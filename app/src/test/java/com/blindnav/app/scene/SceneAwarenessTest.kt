package com.blindnav.app.scene

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * SceneMemory / SpeechPolicy / SceneAwarenessEngine 的行为验证。
 *
 * 最后一组是**真实数据回放**：scene_frames.tsv 是 BLV 连续帧的 VLM 结构化读数
 * （30 段行走视频、150 帧），用它量三件事：
 *   每帧都播 vs 只播变化，差多少条；
 *   变化门能把云端调用压到几帧一次；
 *   去重之后还剩多少"其实没新信息"的播报（这是 SpeechDeduper 那种字符串比对做不到的）。
 */
class SceneAwarenessTest {

    private fun o(cat: String, side: SceneSide = SceneSide.FRONT, st: SceneState = SceneState.STATIC) =
        SceneObject(cat, side, st)

    private fun obs(frame: Long, ts: Long, vararg objs: SceneObject, texts: List<String> = emptyList()) =
        SceneObservation(frame, ts, objs.toList(), texts)

    // ---------------------------------------------------------------- SceneMemory
    @Test
    fun sceneEntryIsAnnouncedOnceThenGoesQuiet() {
        // confirmFrames=1 时，第一帧的目标算"新出现"——这正是"进入新场景"该说的那一句。
        // 但只此一次，下一帧同样的目标不能再报。
        val m = SceneMemory(confirmFrames = 1)
        val d1 = m.observe(obs(1, 1000, o("person"), o("bicycle", SceneSide.RIGHT)))
        assertEquals(2, d1.appeared.size)
        val d2 = m.observe(obs(2, 1200, o("person"), o("bicycle", SceneSide.RIGHT)))
        assertTrue("同一批目标在第二帧不该再报", !d2.hasNews)
    }

    @Test
    fun withTemporalConfirmationFirstFrameIsSilent() {
        // confirmFrames=2 时，刚拿到画面不会立刻播报，等第二次确认
        val m = SceneMemory(confirmFrames = 2)
        val d1 = m.observe(obs(1, 1000, o("person")))
        assertTrue("还没确认，不该报", d1.appeared.isEmpty())
        val d2 = m.observe(obs(2, 1200, o("person")))
        assertEquals("连续两帧确认后作为场景内容报出", listOf("person@FRONT"), d2.appeared.map { it.identity })
    }

    @Test
    fun unchangedSceneStaysSilent() {
        val m = SceneMemory(confirmFrames = 1)
        m.observe(obs(1, 1000, o("person"), o("bicycle", SceneSide.RIGHT)))
        for (i in 2..10) {
            val d = m.observe(obs(i.toLong(), 1000L + i * 200L, o("person"), o("bicycle", SceneSide.RIGHT)))
            assertTrue("同一批目标反复出现不该反复报", !d.hasNews)
        }
    }

    @Test
    fun onlyGenuinelyNewThingsAreNews() {
        val m = SceneMemory(confirmFrames = 1)
        m.observe(obs(1, 1000, o("person")))
        val d = m.observe(obs(2, 1200, o("person"), o("car", SceneSide.LEFT)))
        assertEquals(listOf("car@LEFT"), d.appeared.map { it.identity })
        assertFalse("消失的行人不该算新消息", d.hasNews && d.appeared.isEmpty() && d.stateChanged.isEmpty())
    }

    @Test
    fun singleFrameFlickerIsNotConfirmed() {
        // 检测器单帧误报：只出现一次的 stairs 不该被认定存在
        val m = SceneMemory(confirmFrames = 2, forgetFrames = 2)
        m.observe(obs(1, 1000, o("person")))
        val d = m.observe(obs(2, 1200, o("person"), o("stairs", SceneSide.RIGHT)))
        assertTrue("只出现 1 次的 stairs 不该被认定存在",
            d.appeared.none { it.identity == "stairs@RIGHT" })
        val d2 = m.observe(obs(3, 1400, o("person"), o("stairs", SceneSide.RIGHT)))
        assertEquals("连续 2 次出现后才算存在", listOf("stairs@RIGHT"), d2.appeared.map { it.identity })
    }

    @Test
    fun stateChangeIsNewsButNeedsReconfirm() {
        val m = SceneMemory(confirmFrames = 2)
        m.observe(obs(1, 1000, o("person")))
        m.observe(obs(2, 1200, o("person")))                 // 确认存在
        val flicker = m.observe(obs(3, 1400, o("person", st = SceneState.CROSSING)))
        assertTrue("单帧状态跳变不该立刻播报", flicker.stateChanged.isEmpty() && flicker.appeared.isEmpty())
        val settled = m.observe(obs(4, 1600, o("person", st = SceneState.CROSSING)))
        assertEquals(1, settled.stateChanged.size + settled.appeared.size)
    }

    @Test
    fun disappearanceIsReportedButIsNotNews() {
        val m = SceneMemory(confirmFrames = 1, forgetFrames = 2)
        m.observe(obs(1, 1000, o("person")))
        m.observe(obs(2, 1200))
        val d = m.observe(obs(3, 1400))
        assertEquals(listOf("person@FRONT"), d.disappeared.map { it.identity })
        assertFalse("人走开不该触发播报", d.hasNews)
    }

    @Test
    fun newTextIsNewsOnceOnly() {
        val m = SceneMemory(confirmFrames = 1)
        m.observe(obs(1, 1000, texts = listOf("星巴克", "STARBUCKS")))
        val d2 = m.observe(obs(2, 1200, texts = listOf("星巴克")))
        assertTrue("同一段文字不该重复报", d2.newTexts.isEmpty())
        val d3 = m.observe(obs(3, 1400, texts = listOf("星巴克", "瑞幸咖啡")))
        assertEquals(listOf("瑞幸咖啡"), d3.newTexts)
    }

    @Test
    fun staleFrameIdIgnored() {
        val m = SceneMemory(confirmFrames = 1)
        m.observe(obs(5, 1000, o("person")))
        val back = m.observe(obs(4, 900, o("car")))
        assertTrue("旧帧号不能改状态（与最后10米的证据污染同一类问题）", back.isEmpty)
    }

    // ---------------------------------------------------------------- SpeechPolicy
    private val p = SpeechPolicy(ambientCategoryCooldownMs = 12_000, ambientGlobalCooldownMs = 8_000,
        urgentRepeatMs = 3_000, maxDeferMs = 4_000)

    @Test
    fun urgentAlwaysGetsThroughAndDedupsItself() {
        assertEquals(SpeechAction.SPEAK, p.decide(SpeechRequest(SpeechPriority.URGENT, "正前方有行人", setOf("person"), 1000)).action)
        assertEquals("同一句危险提示 3 秒内不重复",
            SpeechAction.SILENT, p.decide(SpeechRequest(SpeechPriority.URGENT, "正前方有行人", setOf("person"), 2000)).action)
        assertEquals(SpeechAction.SPEAK, p.decide(SpeechRequest(SpeechPriority.URGENT, "正前方有行人", setOf("person"), 5000)).action)
    }

    @Test
    fun ambientNeedsNewsAndCooldown() {
        val q = SpeechPolicy()
        assertEquals(SpeechAction.SILENT, q.decide(SpeechRequest(SpeechPriority.AMBIENT, "右边有自行车",
            setOf("bicycle"), 1000, hasNews = false)).action)
        assertEquals(SpeechAction.SPEAK, q.decide(SpeechRequest(SpeechPriority.AMBIENT, "右边有自行车",
            setOf("bicycle"), 1000)).action)
        assertEquals("全局冷却内应 DEFER",
            SpeechAction.DEFER, q.decide(SpeechRequest(SpeechPriority.AMBIENT, "左边有台阶",
                setOf("stairs"), 3000)).action)
    }

    @Test
    fun answerBypassesAmbientThrottle() {
        val q = SpeechPolicy()
        q.decide(SpeechRequest(SpeechPriority.AMBIENT, "右边有自行车", setOf("bicycle"), 1000))
        val d = q.decide(SpeechRequest(SpeechPriority.ANSWER, "正前方是人行道，左边有便利店", emptySet(), 1500))
        assertEquals("用户主动问必须回答", SpeechAction.SPEAK, d.action)
    }

    @Test
    fun forbiddenPhrasingIsBlockedAtTheExit() {
        // 即使上游模型漏了，播报出口也必须拦住移动/安全类措辞
        for (bad in listOf("往右走两步", "可以直接进去", "前方3米安全", "向左边挪")) {
            val d = p.decide(SpeechRequest(SpeechPriority.URGENT, bad, setOf("person"), 1000))
            assertEquals("越权措辞不该播出去：$bad", SpeechAction.SILENT, d.action)
            assertTrue(d.reason.startsWith("forbidden_phrasing"))
        }
    }

    @Test
    fun sameCategoryThrottledButNewCategoryNot() {
        val q = SpeechPolicy(ambientGlobalCooldownMs = 0, ambientCategoryCooldownMs = 10_000)
        assertEquals(SpeechAction.SPEAK, q.decide(SpeechRequest(SpeechPriority.AMBIENT, "右边有自行车", setOf("bicycle"), 1000)).action)
        assertEquals(SpeechAction.DEFER, q.decide(SpeechRequest(SpeechPriority.AMBIENT, "又有自行车", setOf("bicycle"), 3000)).action)
        assertEquals("新类别不该被旧类别的冷却压住",
            SpeechAction.SPEAK, q.decide(SpeechRequest(SpeechPriority.AMBIENT, "右边有台阶", setOf("stairs"), 3000)).action)
    }

    // ---------------------------------------------------------------- 真实数据回放
    @Test
    fun replayRealFramesAndCountSpeech() {
        val url = javaClass.classLoader!!.getResource("last10m/scene_frames.tsv") ?: return
        val lines = url.openStream().bufferedReader().readLines().filter { it.isNotBlank() && !it.startsWith("#") }
        var naive = 0; var spoken = 0; var frames = 0; var newsFrames = 0; var cooled = 0
        val examples = ArrayList<String>()
        var seq = ""
        // 每段视频是独立场景，各自一套记忆与节流
        var m = SceneMemory(confirmFrames = 1)
        var policy = SpeechPolicy()
        for (line in lines) {
            val f = line.split("\t")
            if (f.size < 4) continue
            if (f[0] != seq) { seq = f[0]; m = SceneMemory(confirmFrames = 1); policy = SpeechPolicy() }
            val ts = f[2].toLong()
            val objs = if (f[3].isBlank()) emptyList() else f[3].split(";").mapNotNull {
                val g = it.split("|")
                if (g.size < 3) return@mapNotNull null
                SceneObject(g[0], runCatching { SceneSide.valueOf(g[1]) }.getOrDefault(SceneSide.UNKNOWN),
                    runCatching { SceneState.valueOf(g[2]) }.getOrDefault(SceneState.STATIC))
            }
            val texts = if (f.size > 4 && f[4].isNotBlank()) f[4].split("|") else emptyList()
            frames++
            naive += objs.size + texts.size
            val d = m.observe(SceneObservation(f[1].toLong(), ts, objs, texts))
            val text = SceneNarrator.narrate(d)
            if (d.hasNews && text.isNotBlank()) {
                newsFrames++
                val dec = policy.decide(SpeechRequest(SpeechPriority.AMBIENT, text,
                    (d.appeared + d.stateChanged.map { it.second }).map { it.category }.toSet(), ts))
                if (dec.action == SpeechAction.SPEAK) {
                    spoken++; if (examples.size < 6) examples.add("[$seq f${f[1]}] $text")
                } else cooled++
            }
        }
        println("SCENE REPLAY 真实帧 $frames 帧（BLV 30 段连续帧的 VLM 结构化读数）")
        println("  不记忆会说出 $naive 条内容；SceneMemory 去重后剩 $newsFrames 帧有新信息 " +
                "→ 记忆本身压掉 ${"%.1f".format(100.0 * (1 - newsFrames.toDouble() / maxOf(1, naive)))}%")
        println("  再过 SpeechPolicy 节流实际批准 $spoken 条（被冷却压住 $cooled 帧）" +
                "→ 端到端压掉 ${"%.1f".format(100.0 * (1 - spoken.toDouble() / maxOf(1, naive)))}%")
        println("  注意：BLV 每段只有 0.3~0.5 秒，8 秒冷却必然只让每段说一条，" +
                "所以端到端那个数偏乐观；真实行走要按『记忆压掉的部分』看。")
        examples.forEach { println("  播报名单 $it") }
        assertTrue("回放里必须真的压掉了大部分播报", spoken * 3 < naive)
        assertTrue("但也不能一条都不播（那是没接通）", spoken > 0)
    }
}
