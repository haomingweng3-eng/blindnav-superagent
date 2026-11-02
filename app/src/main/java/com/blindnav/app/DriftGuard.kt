package com.blindnav.app

import kotlin.math.abs
import kotlin.math.max

/** 行进方向的持续偏差提醒；不判断人体横向位置或两侧是否可走。 */
class DriftGuard {
    data class Notice(val level: String, val side: String, val text: String)
    var ready = false
        private set
    private var scope = ""
    private var reference: Double? = null
    private var lastFix = -1L
    private var side = 0
    private var count = 0
    private var since = 0L
    private var severeCount = 0
    private var severeSince = 0L
    private var goodCount = 0
    private var goodSince = 0L
    private var activeLevel = 0
    private var lastSaid = Long.MIN_VALUE

    fun reset() {
        ready = false; scope = ""; reference = null; lastFix = -1L
        side = 0; count = 0; severeCount = 0; goodCount = 0
        activeLevel = 0; lastSaid = Long.MIN_VALUE
    }

    fun sample(scope: String, now: Long, fix: Long, reference: Double, heading: Double?,
               headingError: Double?, positionError: Double, speed: Double, eligible: Boolean = true): Notice? {
        if (!eligible || now-fix !in 0L..3_000_000_000L || !reference.isFinite() ||
            heading == null || !heading.isFinite() || heading !in 0.0..<360.0 ||
            headingError == null || !headingError.isFinite() || headingError !in 0.0..15.0 ||
            !positionError.isFinite() || positionError !in 0.0..10.0 || !speed.isFinite() || speed !in 0.5..3.5) {
            reset(); return null
        }
        if (this.scope != scope || this.reference?.let { abs(delta(reference,it))>10 } == true ||
            (lastFix>=0 && fix-lastFix>3_000_000_000L)) reset()
        this.scope=scope; this.reference=reference
        if (fix<=lastFix) return null
        lastFix=fix; ready=true
        val diff=delta(heading,reference)
        val magnitude=abs(diff)
        val enter=max(18.0,headingError*2+5)
        val severe=max(40.0,enter+15)
        if (magnitude<=8) {
            count=0; side=0; severeCount=0
            if (goodCount++==0) goodSince=fix
            if (activeLevel>0 && goodCount>=2 && fix-goodSince>=1_000_000_000L) {
                activeLevel=0; lastSaid=fix
                return Notice("recovered","center","方向偏差已减小，仍需用盲杖核对脚下")
            }
            return null
        }
        goodCount=0
        if (magnitude<enter) { count=0; side=0; severeCount=0; return null }
        val currentSide=if(diff>0)1 else -1
        if (side!=currentSide) { side=currentSide; count=0; severeCount=0 }
        if (count++==0) since=fix
        if (magnitude>=severe) { if (severeCount++==0) severeSince=fix } else severeCount=0
        val urgent=severeCount>=2 && fix-severeSince>=1_000_000_000L
        val gentle=count>=3 && fix-since>=2_000_000_000L
        val cooled=lastSaid==Long.MIN_VALUE || fix-lastSaid>=8_000_000_000L
        val escalate=urgent && activeLevel<2
        if ((!urgent && !gentle) || (!escalate && !cooled)) return null
        if (activeLevel==2 && !urgent) return null
        activeLevel=if(urgent)2 else 1; lastSaid=fix
        val word=if(side>0)"右" else "左"
        return Notice(if(urgent)"urgent" else "gentle",if(side>0)"right" else "left",
            if(urgent)"行进方向明显偏${word}，先停下确认" else "行进方向偏${word}，请核对脚下和参考方向")
    }

    private fun delta(a: Double,b: Double)=((a-b+540)%360+360)%360-180
}
