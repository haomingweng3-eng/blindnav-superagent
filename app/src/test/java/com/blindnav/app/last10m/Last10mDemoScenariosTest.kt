package com.blindnav.app.last10m

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Demo 场景回归（任务书第 16/17 节）：把 `assets/demo/last10m/scenarios.json` 整份跑一遍。
 *
 * 这份文件里的图片是真实公开数据（LSVT 街景照片），JSON 是云端 VLM 对这些图片的**真实回答**，
 * 不是手写的期望值。所以这个测试同时在做两件事：
 *  1. 证明整条链在真实画面输入下能从头走到尾（明天演的是这条代码路径）；
 *  2. 守住三条安全线：邻门不能确认、门前有阻挡不能确认、任何一句语音里都不许出现"安全"。
 *
 * 用的是测试资源里同一份文件（打包时是 assets 里那一份），避免"测试绿了但包里没有"。
 */
class Last10mDemoScenariosTest {

    private val json: String by lazy {
        javaClass.classLoader!!.getResourceAsStream("last10m/demo_scenarios.json")!!
            .bufferedReader().readText()
    }
    private val scenarios by lazy { DemoScenarios.parse(json) }

    @Test
    fun scenarioFileLoadsThreeRealScenarios() {
        assertEquals("三个场景：单门确认 / 隔壁门 / 门前有阻挡", 3, scenarios.size)
        assertTrue(scenarios.all { it.frames.isNotEmpty() })
        assertTrue("目标名必须是真的店名：" + scenarios.map { it.targetName },
            scenarios.map { it.targetName }.all { it.length in 3..8 })
        assertTrue("每个场景都要写清来源", scenarios.all { it.provenance.isNotBlank() })
        scenarios.forEach { s ->
            println("  ${s.id} 「${s.title}」 目标=${s.targetName} 帧=${s.frames.size} " +
                s.frames.joinToString(" | ") { f -> f.label })
        }
    }

    @Test
    fun arrivalScenarioReachesConfirmedEntranceThroughRealOutputs() {
        val runner = Last10mDemoRunner(scenarios.first { it.id == "s1_arrival" })
        val steps = ArrayList<Last10mDemoRunner.FrameOutcome>()
        while (true) { val o = runner.step() ?: break; steps.add(o) }
        assertTrue("至少要有两帧", steps.size >= 2)
        steps.forEach { println("  S1 ${it.label}: state=${it.state} decision=${it.decision} " +
                "doors=${it.doors} relation=${it.relationSummary} ground=${it.ground} " +
                "needView=${it.needView} 说=\"${it.spoken}\"") }
        val first = steps.first()
        assertEquals("第一帧的来源必须是 REPLAY，不能冒充实时识别", "REPLAY", first.source)
        assertTrue("第一帧就该看到候选门", first.doors >= 1)
        val last = steps.last()
        assertEquals("收窄取景后应能确认目标那一扇并到达",
            ArrivalDecision.ARRIVAL_CONFIRMED, last.decision)
        assertEquals(Last10mState.ARRIVAL_CONFIRMED, last.state)
        assertTrue("整场至少要说一次到达（但不能每帧都说一遍）：" + steps.map { it.spoken },
            steps.any { it.spoken.contains("入口已确认") })
        assertFalse("任何一句都不许出现安全承诺", steps.any { it.spoken.contains("安全") })
        assertFalse("每帧都必须说清来源链", steps.any { it.trail.isBlank() })
    }

    @Test
    fun neighbourScenarioNeverConfirmsEvenThoughItSeesTheTarget() {
        val runner = Last10mDemoRunner(scenarios.first { it.id == "s2_neighbor" })
        val steps = runAll(runner)
        assertTrue("这个场景至少两帧（远景先看店面，近景再看那一扇）", steps.size >= 2)
        steps.forEach { println("  S2 ${it.label}: decision=${it.decision} relation=${it.relationSummary} " +
                "reasons=${it.reasons} 说=\"${it.spoken}\"") }
        // 关键不变量：**每一帧**都不能确认到达；看到店名不等于找到入口。
        steps.forEach { assertTrue("${it.label} 绝不能宣布到达", it.decision != ArrivalDecision.ARRIVAL_CONFIRMED) }
        assertTrue("任何一扇候选门都不能被判成目标门：" +
            runner.machine.candidates().map { "${it.doorId}=${it.relationConfirmed}" },
            runner.machine.candidates().none { it.relationConfirmed == true })
        assertTrue("目标确实被看到了（否则这场景没测到东西）",
            steps.any { it.reasons.none { r -> r == ReasonCode.TARGET_NOT_FOUND } })
        val last = steps.last()
        assertTrue("最后一帧要给一句能听懂的话：${last.spoken}", last.spoken.isNotBlank())
        assertFalse(steps.any { it.spoken.contains("安全") })
    }

    @Test
    fun hazardScenarioRefusesArrivalAndSaysPossibleNotSafe() {
        val runner = Last10mDemoRunner(scenarios.first { it.id == "s3_hazard" })
        val steps = ArrayList<Last10mDemoRunner.FrameOutcome>()
        while (true) { val o = runner.step() ?: break; steps.add(o) }
        steps.forEach { println("  S3 ${it.label}: decision=${it.decision} ground=${it.ground} " +
                "type=${it.doorType} 说=\"${it.spoken}\"") }
        assertTrue("两帧都要拒绝确认", steps.all { it.decision != ArrivalDecision.ARRIVAL_CONFIRMED })
        assertTrue("至少一帧要把门前风险说出来：" + steps.map { it.spoken },
            steps.any { it.spoken.contains("挡") || it.spoken.contains("台阶") || it.spoken.contains("路缘") })
        assertFalse("风险提示只能是\"可能\"，不能说安全", steps.any { it.spoken.contains("安全") })
        // 人工补一条台阶声明：来源必须变成 DEBUG_MANUAL，措辞也不能升级成安全
        assertTrue(runner.declareGroundManually(step = true))
        val after = runner.machine.evaluate(90_000L, emptyList())
        assertNotEqualsArrival(after.decision)
        println("  S3 人工补台阶后 decision=${after.decision} reasons=${after.reasonCodes}")
    }

    @Test
    fun everyFrameOutcomeCarriesSourceAndNeedView() {
        for (scenario in scenarios) {
            val runner = Last10mDemoRunner(scenario)
            var out = runner.step()
            while (out != null) {
                assertNotNull(out.state)
                assertTrue("${scenario.id}/${out.label} 没有 needView",
                    out.needView != null)
                assertTrue("${scenario.id}/${out.label} 缺来源", out.source.isNotBlank())
                assertTrue("${scenario.id}/${out.label} 顶部三行不完整：${out.header}",
                    out.header.contains("目标：") && out.header.contains("当前：") && out.header.contains("下一步："))
                assertFalse("${scenario.id}/${out.label} 顶部出现了移动指令：${out.header}",
                    listOf("往左走", "往右走", "向前走", "米").any { out.header.contains(it) })
                out = runner.step()
            }
        }
        println("DEMO 全部帧都带来源、needView 与人话三行")
    }

    @Test
    fun replayReasonerIsDeterministicSoTheDemoCannotDrift() {
        val scenario = scenarios.first()
        val a = runAll(Last10mDemoRunner(scenario))
        val b = runAll(Last10mDemoRunner(scenario))
        assertEquals("同一场景重放必须得到同样的决策序列", a.map { it.decision }, b.map { it.decision })
        assertEquals(a.map { it.spoken }, b.map { it.spoken })
        println("DEMO 可重放：${a.map { it.decision.name }}")
    }

    private fun runAll(runner: Last10mDemoRunner): List<Last10mDemoRunner.FrameOutcome> {
        val out = ArrayList<Last10mDemoRunner.FrameOutcome>()
        while (true) { val o = runner.step() ?: break; out.add(o) }
        return out
    }

    private fun assertNotEqualsArrival(d: ArrivalDecision) {
        assertTrue("绝不能宣布到达", d != ArrivalDecision.ARRIVAL_CONFIRMED)
    }
}
