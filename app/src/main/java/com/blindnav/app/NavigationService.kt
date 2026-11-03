package com.blindnav.app

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.location.Location
import android.location.LocationListener
import android.location.LocationManager
import android.os.Build
import android.os.IBinder
import android.os.SystemClock
import org.json.JSONArray
import org.json.JSONObject

/**
 * 导航播报的后台服务：锁屏/切到后台也继续算距离、放提示音、说话。
 * 网页层只负责"听懂"和"规划"，行走中的持续播报在这里做。
 */
class NavigationService : Service(), LocationListener {

    private var steps: JSONArray? = null
    private var idx = 0
    private var destName = ""
    private var destLng = 0.0
    private var destLat = 0.0
    private var lastTurn = -1
    private var lastMid = 0L
    private var lastMileMark = 999
    private var lm: LocationManager? = null
    private var offCount = 0
    private var lastOffSay = 0L
    private var lastFixNanos = 0L
    private var qualityWarnAt = 0L
    private var endpointStrikes = 0
    private var routeId = ""
    private var lastMapLng = 0.0
    private var lastMapLat = 0.0
    private var lastAccuracy = 0f
    private var lastFixWall = 0L
    private var serviceTicket = -1L
    private var lastWalkHintStep = -1
    private val driftGuard = DriftGuard()
    private val straightReassurance = StraightReassurancePolicy()
    private val directionConfidence = DirectionConfidence()
    private var directionConfidenceLevel = "low"
    private var directionConfidenceReason = "方向尚未校准"
    private var lastDirectionConfidenceSay = 0L
    private var lastTravelError: Double? = null
    @Volatile private var cancelled = false
    private var sm: SensorManager? = null
    private val rot = FloatArray(9)
    private val ang = FloatArray(3)

    @Volatile private var azimuth: Float? = null

    private val sensorListener = object : SensorEventListener {
        override fun onSensorChanged(e: SensorEvent) {
            if (e.sensor.type == Sensor.TYPE_ROTATION_VECTOR) {
                SensorManager.getRotationMatrixFromVector(rot, e.values)
                SensorManager.getOrientation(rot, ang)
                val deg = ((Math.toDegrees(ang[0].toDouble()).toFloat()) + 360f) % 360f
                val prev = azimuth
                // 手持时磁方位会飘，平滑一下再拿去算方向音
                azimuth = if (prev == null) deg else {
                    val d = ((deg - prev + 540f) % 360f) - 180f
                    (prev + d * 0.2f + 360f) % 360f
                }
            }
        }
        override fun onAccuracyChanged(sensor: Sensor?, accuracy: Int) {}
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.getBooleanExtra("stop", false) == true) { cancel(); stopSelf(); return START_NOT_STICKY }
        val ticket = intent?.getLongExtra("safetyToken", -1L) ?: -1L
        val accepted = NativeSafety.navigation.perform(ticket) {
            serviceTicket = ticket
            runningInstance = this
            cancelled = false
            clearEndedSession()
            intent?.let { i ->
                destName = i.getStringExtra("dest") ?: ""
                destLng = i.getDoubleExtra("lng", 0.0)
                destLat = i.getDoubleExtra("lat", 0.0)
                directionConfidence.setMountMode(i.getStringExtra("mountMode") ?: requestedMountMode)
                i.getStringExtra("steps")?.let {
                    steps = runCatching { JSONArray(it) }.getOrNull()
                    idx = 0; lastTurn = -1; lastMid = 0L; lastMileMark = 999
                    lastWalkHintStep = -1
                    driftGuard.reset(); directionConfidence.reset(); straightReassurance.reset(); lastTravelError = null
                    offCount = 0; lastOffSay = 0L; endpointStrikes = 0; lastFixNanos = 0L
                    routeId = steps?.optJSONObject(0)?.optString("routeId") ?: ""
                    navigationState = JSONObject().put("routeId", routeId).put("active", true)
                        .put("valid", false).put("index", 0).put("t", System.currentTimeMillis()).toString()
                }
            }
        }
        if (!accepted || cancelled) { stopSelf(); return START_NOT_STICKY }
        if (steps == null || steps!!.length() == 0) { stopSelf(); return START_NOT_STICKY }
        TtsEngine.init(this)
        startForegroundCompat()
        startLocation()
        // 没有持久化路线时不能在进程重建后假装恢复了导航。
        return START_NOT_STICKY
    }

    private fun startForegroundCompat() {
        val ch = "nav"
        val nm = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        if (Build.VERSION.SDK_INT >= 26) {
            nm.createNotificationChannel(
                NotificationChannel(ch, "导航", NotificationManager.IMPORTANCE_LOW)
            )
        }
        @Suppress("DEPRECATION")
        val builder: Notification.Builder =
            if (Build.VERSION.SDK_INT >= 26) Notification.Builder(this, ch)
            else Notification.Builder(this)
        val notif = builder
            .setContentTitle("正在导航到" + destName)
            .setContentText("后台持续播报，可以锁屏")
            .setSmallIcon(android.R.drawable.ic_dialog_map)
            .build()
        if (Build.VERSION.SDK_INT >= 29)
            startForeground(1, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_LOCATION)
        else startForeground(1, notif)
    }

    private fun startLocation() {
        val mgr = getSystemService(Context.LOCATION_SERVICE) as LocationManager
        lm = mgr
        try {
            mgr.requestLocationUpdates(LocationManager.GPS_PROVIDER, 1000L, 0f, this)
            mgr.requestLocationUpdates(LocationManager.NETWORK_PROVIDER, 2000L, 5f, this)
        } catch (e: SecurityException) { }

        // GPS 航向静止时无效，用陀螺仪融合的磁方位补上，方向音站着听也有意义
        sm = getSystemService(Context.SENSOR_SERVICE) as SensorManager
        sm?.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)?.let {
            sm?.registerListener(sensorListener, it, SensorManager.SENSOR_DELAY_UI)
        }
    }

    override fun onLocationChanged(loc: Location) {
        if (cancelled || !NativeSafety.navigation.responseAllowed(serviceTicket)) return
        val arr = steps ?: return
        if (idx >= arr.length()) return
        val ageNanos = SystemClock.elapsedRealtimeNanos() - loc.elapsedRealtimeNanos
        if (!loc.hasAccuracy() || !loc.accuracy.isFinite() || loc.accuracy < 0 ||
            loc.accuracy > 25 || ageNanos !in 0L..10_000_000_000L) {
            driftGuard.reset(); directionConfidence.reset(); lastTravelError = null; clearDirectionCues()
            publishState(false, null, null, null, null)
            if (System.currentTimeMillis() - qualityWarnAt > 30000) {
                qualityWarnAt = System.currentTimeMillis()
                say("定位暂时不可靠，已暂停转弯和到达判断")
            }
            endpointStrikes = 0
            offCount = 0
            return
        }
        if (loc.elapsedRealtimeNanos <= lastFixNanos) return
        lastFixNanos = loc.elapsedRealtimeNanos
        val mapped = runCatching { MapCoordinates.fromGps(this, loc.longitude, loc.latitude) }.getOrNull()
        if (mapped == null) { driftGuard.reset(); lastTravelError=null; clearDirectionCues(); publishState(false, null, null, null, null); return }
        val lng = mapped.first; val lat = mapped.second
        lastMapLng=lng;lastMapLat=lat;lastAccuracy=loc.accuracy
        lastFixWall=System.currentTimeMillis()-ageNanos/1_000_000L
        val st = arr.getJSONObject(idx)
        var projection = projectStep(st, lng, lat)
        val endDistance = hav(lng, lat, st.getDouble("lng"), st.getDouble("lat"))
        val next = if (idx + 1 < arr.length()) projectStep(arr.getJSONObject(idx + 1), lng, lat) else null
        val passed = next != null && projection[1] < 20 && next[0] < projection[0] &&
                next[0] < 15 && next[3] > 2
        val atEnd = endDistance < 8 && projection[1] < 12 && projection[0] < 15
        endpointStrikes = if (atEnd || passed) endpointStrikes + 1 else 0
        val travel = if (loc.hasBearing() && loc.bearing.isFinite() && loc.bearing in 0f..<360f &&
            loc.hasSpeed() && loc.speed.isFinite() && loc.speed > 0.5f && loc.hasBearingAccuracy() &&
            loc.bearingAccuracyDegrees.isFinite() && loc.bearingAccuracyDegrees in 0f..30f) loc.bearing.toDouble() else null
        lastTravelError = if (travel!=null) loc.bearingAccuracyDegrees.toDouble() else null
        val off = offRouteMeters(lng, lat)
        val pathEvidence = runCatching { JSONObject(CameraActivity.instance?.pathState() ?: "") }.getOrNull()
        val pathSide = pathEvidence?.optString("pathSide", "unknown") ?: "unknown"
        val pathStableFrames = pathEvidence?.optInt("stableFrames", 0) ?: 0
        val pathAligned = pathSide == "center" && pathEvidence?.optString("curve", "none") == "none" && pathStableFrames >= 2
        val pathScore = pathEvidence?.optDouble("score", 0.0)?.takeIf { it.isFinite() } ?: 0.0
        // Chest/neck mounting gives the camera a stable body reference. When
        // the path model has no usable tactile-path observation, allow a
        // separate route-only reference only if the camera/ground/obstacle
        // channels are healthy and clear; handheld mode remains excluded in
        // DirectionConfidence.
        val routeOnly = directionConfidence.mountMode() == "chest" &&
            pathEvidence == null && loc.accuracy <= 10f && loc.speed >= 0.8f &&
            CameraActivity.instance?.allowsDirectionCue() == true
        val confidence = directionConfidence.evaluate(
            travel, lastTravelError, azimuth?.toFloat(), pathSide,
            if (pathAligned) pathScore else 0.0, loc.speed.toDouble(), "$routeId:$idx", routeOnly
        )
        directionConfidenceLevel = confidence.level
        directionConfidenceReason = confidence.reason
        val routeAction = if (st.optInt("walkType", -1) == 1) "过马路" else pickAction(st.optString("instruction"))
        val directionEligible=directionAssistEnabled && confidence.canGuide && projection[1]>20 && off<=20 && st.optInt("walkType",-1) !in setOf(1,8,9,20) &&
            !Regex("过马路|人行横道|斑马线|台阶|阶梯").containsMatchIn(st.optString("instruction")) &&
            CameraActivity.instance?.allowsDirectionCue()!=false
        if (!confidence.canGuide) clearDirectionCues()
        if (confidence.level == "low" && System.currentTimeMillis() - lastDirectionConfidenceSay > 30_000L) {
            lastDirectionConfidenceSay = System.currentTimeMillis()
            say("方向参考暂不可靠，请先停下校准")
        }
        driftGuard.sample("$routeId:$idx",SystemClock.elapsedRealtimeNanos(),loc.elapsedRealtimeNanos,
            projection[2],travel,lastTravelError,loc.accuracy.toDouble(),loc.speed.toDouble(),directionEligible)
            ?.let { sayDirection(it,System.nanoTime()-ageNanos) }
        if (straightReassurance.shouldSpeak(
                projection[1],
                eligible = directionEligible && confidence.level == "high",
                straightSegment = routeAction.isEmpty() || routeAction == "直行"
            )) say("继续保持当前方向")
        if (!driftGuard.ready) clearDirectionCues()
        if (off <= 40) announceWalkType(st)
        if (off > 45 && loc.accuracy <= 20) offCount++ else offCount = 0
        if (offCount >= 3 && System.currentTimeMillis() - lastOffSay > 60000) {
            lastOffSay = System.currentTimeMillis(); offCount = 0
            say("偏离了规划路线，请先停下确认所处位置，再重新规划")
        }
        val action = routeAction
        val finalArea = announceFinalApproach(projection[1], off, loc.accuracy)
        if (endpointStrikes >= 2) {
            if (idx == arr.length() - 1) {
                if (loc.accuracy <= 10) {
                    publishState(true, 0.0, off, projection[2], travel, false, true, 0.0)
                    if (lastTurn < 3) say("地图步行路线已结束。"+routeEndpointNote()+"入口尚未确认。请在安全位置停下，说找入口")
                    stopSelf()
                    return
                }
            } else {
                if (st.optInt("walkType", -1) == 1) say("地图标注该路段是人行横道，请先确认当前位置和过街条件")
                else if (action == "过马路") say("前方是过街路段，请停在路边确认过街条件")
                else if (action.isNotEmpty() && action != "直行") say("现在" + action)
                idx++; endpointStrikes = 0; lastTurn = -1; straightReassurance.reset()
                projection = projectStep(arr.getJSONObject(idx), lng, lat)
                val environmentSaid=announceWalkType(arr.getJSONObject(idx))
                val nowFinalArea=announceFinalApproach(projection[1], off, loc.accuracy)
                if (!nowFinalArea && action != "过马路" && !environmentSaid) say("接下来沿当前路段走 " + projection[1].round() + " 米")
            }
        } else if (!finalArea && st.optInt("walkType", -1) != 1) {
            val remaining = projection[1]
            val tip = if (action.isNotEmpty() && action != "直行") "，路口" + action else ""
            if (remaining < 20 && lastTurn < 2) {
                say("前方接近路段终点" + tip)
                lastTurn = 2
            } else if (remaining < 50 && lastTurn < 1) {
                say("再走 " + remaining.round() + " 米" + tip)
                lastTurn = 1
            }
        }
        var remain = projection[1]
        for (i in idx + 1 until arr.length()) remain += stepLength(arr.getJSONObject(i))
        publishState(true, remain, off, projection[2], travel, stepRemaining = projection[1])
        val mark = (remain / 200).toInt()
        if (mark < lastMileMark && remain > 200) {
            lastMileMark = mark
            say("还有 " + remain.round() + " 米")
        }
    }

    private fun routeEndpointNote(): String {
        val arr=steps ?: return "暂不能核对地图路线终点与目的地坐标是否一致。"
        val end=if (arr.length()>0) arr.optJSONObject(arr.length()-1) else null
        val lng=end?.optDouble("lng",Double.NaN) ?: Double.NaN
        val lat=end?.optDouble("lat",Double.NaN) ?: Double.NaN
        fun valid(x:Double,y:Double)=x.isFinite()&&y.isFinite()&&kotlin.math.abs(x)<=180&&kotlin.math.abs(y)<=90&&!(x==0.0&&y==0.0)
        if (!valid(lng,lat)||!valid(destLng,destLat)) return "暂不能核对地图路线终点与目的地坐标是否一致。"
        return if(hav(lng,lat,destLng,destLat)>20) "地图路线终点与目的地坐标相差较大，不能确认入口距离。" else ""
    }

    private fun announceFinalApproach(remaining: Double, off: Double, accuracy: Float): Boolean {
        val arr=steps ?: return false
        val near=idx==arr.length()-1 && remaining<=20 && off<=20
        if (near && lastTurn<3) {
            val quality=if(accuracy>10) "定位误差较大，不能确认入口距离。" else ""
            val endpointNote=routeEndpointNote()
            val location=if(endpointNote.isNotEmpty()) endpointNote+"入口尚未确认。"
                else if(quality.isNotEmpty()) "" else "已接近地图终点，入口尚未确认。"
            say(quality+location+"请在安全位置停下，说找入口")
            lastTurn=3
        }
        return near
    }

    private fun announceWalkType(st: JSONObject): Boolean {
        if (lastWalkHintStep == idx) return false
        val text = when (st.optInt("walkType", -1)) {
            1 -> "地图标注当前路段有人行横道，请停在路边确认过街条件"
            3 -> "地图标注当前路段有地下通道，入口情况需要现场确认"
            4 -> "地图标注当前路段有过街天桥，入口情况需要现场确认"
            5 -> "地图标注当前路段有地铁通道，入口情况需要现场确认"
            8 -> "地图标注当前路段有扶梯，入口和运行方向需要现场确认"
            9 -> "地图标注当前路段有直梯，能否使用需要现场确认"
            20 -> "地图标注当前路段有阶梯，请先用盲杖确认台阶"
            21 -> "地图标注当前路段有斜坡，路面情况需要现场确认"
            else -> return false
        }
        lastWalkHintStep = idx
        say(text)
        return true
    }

    private fun pickAction(ins: String): String {
        val keys = listOf("左转", "右转", "过马路", "直行", "上坡", "下坡")
        for (k in keys) if (ins.contains(k)) return k
        return ""
    }

    private fun say(t: String) {
        NativeSafety.navigation.perform(serviceTicket) { if (!cancelled) TtsEngine.speak(t) }
    }

    private fun sayDirection(notice: DriftGuard.Notice, observedAt: Long) {
        NativeSafety.navigation.perform(serviceTicket) {
            if (!cancelled) TtsEngine.speakDirection(notice.text,observedAt,notice.level=="urgent")
        }
    }

    private fun clearDirectionCues() {
        NativeSafety.navigation.perform(serviceTicket) { if (!cancelled) TtsEngine.invalidateDirection() }
    }

    private fun Double.round(): Int = Math.round(this).toInt()

    /** 始终使用固定的路线折线，不把当前定位拼成路线的起点。 */
    private fun offRouteMeters(lng: Double, lat: Double): Double {
        val arr = steps ?: return 0.0
        var best = Double.MAX_VALUE
        for (i in 0 until arr.length()) {
            val st = arr.getJSONObject(i)
            best = minOf(best, projectStep(st, lng, lat)[0])
        }
        return if (best.isFinite()) best else 0.0
    }

    private fun stepPoints(st: JSONObject): List<Pair<Double, Double>> {
        val points = st.optJSONArray("points") ?: return emptyList()
        return (0 until points.length()).mapNotNull { i ->
            val p = points.optJSONObject(i) ?: return@mapNotNull null
            val lng = p.optDouble("lng", Double.NaN); val lat = p.optDouble("lat", Double.NaN)
            if (lng.isFinite() && lat.isFinite() && lng in -180.0..180.0 && lat in -90.0..90.0) lng to lat else null
        }
    }

    private fun stepLength(st: JSONObject): Double {
        val pts = stepPoints(st)
        return if (pts.size >= 2) pts.zipWithNext().sumOf { (a, b) -> hav(a.first, a.second, b.first, b.second) }
            else st.optDouble("distance", 0.0)
    }

    /** [离折线距离, 沿线剩余距离, 当前线段方位, 沿线已走距离]。 */
    private fun projectStep(st: JSONObject, lng: Double, lat: Double): DoubleArray {
        val pts = stepPoints(st)
        if (pts.size < 2) {
            val endLng = st.getDouble("lng"); val endLat = st.getDouble("lat")
            val d = hav(lng, lat, endLng, endLat)
            return doubleArrayOf(d, d, brg(lng, lat, endLng, endLat), 0.0)
        }
        val total = stepLength(st)
        var before = 0.0
        var best = doubleArrayOf(Double.MAX_VALUE, total, 0.0, 0.0)
        val mx = 111320.0 * Math.cos(Math.toRadians(lat))
        for (i in 1 until pts.size) {
            val a = pts[i - 1]; val b = pts[i]
            val dx = (b.first - a.first) * mx; val dy = (b.second - a.second) * 111320.0
            val len2 = dx * dx + dy * dy
            val length = hav(a.first, a.second, b.first, b.second)
            if (len2 < 1e-6) continue
            val x = (lng - a.first) * mx; val y = (lat - a.second) * 111320.0
            val fraction = ((x * dx + y * dy) / len2).coerceIn(0.0, 1.0)
            val off = Math.hypot(x - fraction * dx, y - fraction * dy)
            val along = before + fraction * length
            if (off < best[0]) best = doubleArrayOf(off, total - along, brg(a.first, a.second, b.first, b.second), along)
            before += length
        }
        if (best[0] == Double.MAX_VALUE) {
            val endLng = st.getDouble("lng"); val endLat = st.getDouble("lat")
            val d = hav(lng, lat, endLng, endLat)
            return doubleArrayOf(d, d, brg(lng, lat, endLng, endLat), 0.0)
        }
        return best
    }

    private fun sessionSnapshot(state: JSONObject, phase: String): String {
        val route=steps ?: return "{}"
        if(route.length()==0 || routeId.isBlank()) return "{}"
        val arrival=route.optJSONObject(0)?.optJSONObject("arrival")
        return runCatching {
            JSONObject().put("version",1).put("phase",phase).put("routeId",routeId)
                .put("target",JSONObject().put("name",destName).put("lng",destLng).put("lat",destLat)
                    .put("arrivalSource",arrival?.optString("source","restored_unknown") ?: "restored_unknown")
                    .put("poiId",arrival?.optString("poiId","") ?: ""))
                .put("steps",route).put("state",state).toString()
        }.getOrDefault("{}")
    }

    private fun publishState(valid: Boolean, remaining: Double?, off: Double?, bearing: Double?,
                             travel: Double?, active: Boolean = true, arrived: Boolean = false,
                             stepRemaining: Double? = null) {
        if (cancelled) return
        NativeSafety.navigation.perform(serviceTicket) {
            if (cancelled) return@perform
            navigationState = JSONObject().put("routeId", routeId).put("index", idx)
                .put("valid", valid).put("active", active).put("arrived", arrived).put("cancelled",false)
                .put("remaining", remaining ?: JSONObject.NULL).put("offRoute", off ?: JSONObject.NULL)
                .put("stepRemaining", stepRemaining ?: JSONObject.NULL)
                .put("bearing", bearing ?: JSONObject.NULL).put("travelHeading", travel ?: JSONObject.NULL)
                .put("travelHeadingAccuracy",lastTravelError ?: JSONObject.NULL)
                .put("directionGuardNative",true).put("directionGuardReady",driftGuard.ready)
                .put("directionGuardEnabled",directionAssistEnabled)
                .put("directionConfidence",directionConfidenceLevel).put("directionConfidenceReason",directionConfidenceReason)
                .put("mountMode", directionConfidence.mountMode())
                .put("lng",lastMapLng).put("lat",lastMapLat).put("accuracy",lastAccuracy).put("fixTime",lastFixWall)
                .put("t", System.currentTimeMillis()).toString()
            if (arrived && !active && runningInstance === this) {
                endedSnapshot=sessionSnapshot(JSONObject(navigationState),"route_ended")
                endedTicket=serviceTicket
            }
        }
    }

    private fun segDist(px: Double, py: Double, ax: Double, ay: Double, bx: Double, by: Double): Double {
        val mLat = 111320.0
        val mLng = 111320.0 * Math.cos(Math.toRadians(py))
        val x = (px - ax) * mLng; val y = (py - ay) * mLat
        val dx = (bx - ax) * mLng; val dy = (by - ay) * mLat
        val len2 = dx * dx + dy * dy
        val t = if (len2 == 0.0) 0.0 else ((x * dx + y * dy) / len2).coerceIn(0.0, 1.0)
        val cx = x - t * dx; val cy = y - t * dy
        return Math.sqrt(cx * cx + cy * cy)
    }

    private fun hav(l1: Double, a1: Double, l2: Double, a2: Double): Double {
        val r = 6371000.0
        val p1 = Math.toRadians(a1); val p2 = Math.toRadians(a2)
        val dp = p2 - p1; val dl = Math.toRadians(l2 - l1)
        val x = Math.sin(dp / 2) * Math.sin(dp / 2) +
                Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) * Math.sin(dl / 2)
        return 2 * r * Math.asin(Math.sqrt(x))
    }

    private fun brg(l1: Double, a1: Double, l2: Double, a2: Double): Double {
        val p1 = Math.toRadians(a1); val p2 = Math.toRadians(a2)
        val dl = Math.toRadians(l2 - l1)
        val y = Math.sin(dl) * Math.cos(p2)
        val x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl)
        return (Math.toDegrees(Math.atan2(y, x)) + 360) % 360
    }

    override fun onStatusChanged(provider: String?, status: Int, extras: android.os.Bundle?) {}
    override fun onProviderEnabled(provider: String) {}
    override fun onProviderDisabled(provider: String) {}

    companion object {
        @Volatile var directionAssistEnabled = true
            private set
        @Volatile private var requestedMountMode = "handheld"
        @Volatile private var endedSnapshot="{}"
        @Volatile private var endedTicket=-1L
        @Volatile private var runningInstance: NavigationService? = null
        @Volatile var navigationState: String = "{}"
            private set

        /**
         * 只读：当前导航的目的地名称。没有导航（或已取消）就是空串。
         * 最后十米用它当"选择目标"的默认值，不改任何导航行为。
         */
        fun lastDestName(): String =
            runningInstance?.takeIf { !it.cancelled }?.destName?.takeIf { it.isNotBlank() } ?: ""

        /**
         * 只读：目标在哪个**地图方位**（正北 0°，顺时针）。
         *
         * 这不是"该往哪走"的指令，只用来决定"先往哪边扫招牌"；
         * 任何移动决定都不由这里产生。离终点 120 m 以外返回 null ——
         * 那时候"目标在西北"对门口没有任何操作意义，宁可让状态机说"方向未知"。
         */
        fun lastTargetBearingDeg(): Double? {
            val i = runningInstance ?: return null
            if (i.cancelled || i.destLng == 0.0 || i.destLat == 0.0) return null
            if (i.lastMapLng == 0.0 || i.lastMapLat == 0.0) return null
            val d = i.hav(i.lastMapLng, i.lastMapLat, i.destLng, i.destLat)
            if (d > 120.0) return null
            return i.brg(i.lastMapLng, i.lastMapLat, i.destLng, i.destLat)
        }

        /** 只读：当前位置到目的地的直线距离（米）；没有定位/目的地就 null。 */
        fun lastTargetDistanceM(): Double? {
            val i = runningInstance ?: return null
            if (i.cancelled || i.destLng == 0.0 || i.destLat == 0.0) return null
            if (i.lastMapLng == 0.0 || i.lastMapLat == 0.0) return null
            return i.hav(i.lastMapLng, i.lastMapLat, i.destLng, i.destLat)
        }

        /** Read only: a page may reattach to a live service, never restart it. */
        fun liveSessionSnapshot(): String {
            val instance=runningInstance
            val ticket=instance?.serviceTicket ?: endedTicket
            var result="{}"
            NativeSafety.navigation.perform(ticket) {
                if(runningInstance !== instance) return@perform
                val state=runCatching { JSONObject(navigationState) }.getOrNull() ?: return@perform
                if(state.optBoolean("cancelled")) return@perform
                if(instance!=null && !instance.cancelled && state.optBoolean("active") &&
                    state.optString("routeId")==instance.routeId) {
                    result=instance.sessionSnapshot(state,"active")
                } else if (endedTicket==ticket && !state.optBoolean("active") && state.optBoolean("arrived")) {
                    val ended=runCatching { JSONObject(endedSnapshot) }.getOrNull()
                    if(ended?.optString("routeId")==state.optString("routeId") && ended?.optString("phase")=="route_ended") result=endedSnapshot
                }
            }
            return result
        }

        private fun clearEndedSession() {
            endedSnapshot="{}"
            endedTicket=-1L
        }


        /** MainActivity桥在主线程调用，与定位回调串行。 */
        fun setDirectionAssist(enabled: Boolean) {
            directionAssistEnabled=enabled
            runningInstance?.driftGuard?.reset()
            if (!enabled) TtsEngine.invalidateDirection()
        }

        /** Selects the physical camera placement; changing it clears calibration. */
        fun setMountMode(mode: String?) {
            requestedMountMode = if (mode == "chest") "chest" else "handheld"
            runningInstance?.directionConfidence?.setMountMode(requestedMountMode)
            runningInstance?.driftGuard?.reset()
            if (runningInstance != null) TtsEngine.invalidateDirection()
        }

        fun cancel() {
            val token = NativeSafety.navigation.beginSafetyCommand()
            runningInstance?.cancelled = true
            clearEndedSession()
            runCatching { navigationState = JSONObject(navigationState).put("active", false).put("arrived",false).put("cancelled",true).toString() }
            NativeSafety.navigation.acknowledge(token)
        }
    }

    override fun onDestroy() {
        cancelled = true
        if (runningInstance === this) runningInstance = null
        runCatching {
            val state=JSONObject(navigationState)
            if (state.optString("routeId")==routeId) navigationState = state.put("active", false).toString()
        }
        runCatching { lm?.removeUpdates(this) }
        runCatching { sm?.unregisterListener(sensorListener) }
        super.onDestroy()
    }
}
