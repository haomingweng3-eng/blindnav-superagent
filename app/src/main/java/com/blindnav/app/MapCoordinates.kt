package com.blindnav.app

import android.content.Context
import com.amap.api.maps.CoordinateConverter
import com.amap.api.maps.model.LatLng

/** Android/WebView 的 GPS 坐标统一转换成高德路线所用的地图坐标。 */
object MapCoordinates {
    fun fromGps(context: Context, lng: Double, lat: Double): Pair<Double, Double> {
        require(lng.isFinite() && lat.isFinite() && lng in -180.0..180.0 && lat in -90.0..90.0)
        val point = CoordinateConverter(context)
            .from(CoordinateConverter.CoordType.GPS)
            .coord(LatLng(lat, lng))
            .convert()
        return point.longitude to point.latitude
    }
}
