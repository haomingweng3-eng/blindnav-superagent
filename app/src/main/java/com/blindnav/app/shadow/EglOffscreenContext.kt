package com.blindnav.app.shadow

import android.opengl.EGL14
import android.opengl.EGLConfig
import android.opengl.EGLContext
import android.opengl.EGLDisplay
import android.opengl.EGLSurface
import android.opengl.GLES20

/**
 * 无界面 EGL 上下文。
 *
 * 为什么需要它：ARCore 1.57 的 `Session.update()` 官方注释写明会抛
 * `MissingGlContextException`（"if there is no OpenGL context available"），
 * 即使我们只要 CPU 图像、不画任何东西。生产 App 现在完全没有 GL 代码
 * （UI 是 WebView + FrameLayout），所以影子链路必须自带一个离屏上下文，
 * 而不是去给 CameraActivity 加 GLSurfaceView 改生产 UI。
 *
 * 同时按官方要求给 ARCore 一个 `GL_TEXTURE_EXTERNAL_OES` 纹理号。
 * 我们不消费这张纹理，ARCore 仍会往里上传相机帧——这是一笔真实的 GPU 开销，
 * 会在 thermal/FPS 记录里体现，属于"ARCore 当主时钟"的代价之一，需要真机数据确认。
 */
class EglOffscreenContext {

    private var display: EGLDisplay = EGL14.EGL_NO_DISPLAY
    private var context: EGLContext = EGL14.EGL_NO_CONTEXT
    private var surface: EGLSurface = EGL14.EGL_NO_SURFACE
    private var config: EGLConfig? = null

    var textureId: Int = 0
        private set

    /** @return null 表示成功，否则返回可读的失败原因（影子链路要求不 crash）。 */
    fun create(): String? {
        display = EGL14.eglGetDisplay(EGL14.EGL_DEFAULT_DISPLAY)
        if (display === EGL14.EGL_NO_DISPLAY) return "EGL_NO_DISPLAY"
        val version = IntArray(2)
        if (!EGL14.eglInitialize(display, version, 0, version, 1)) return "eglInitialize_failed"
        val attribs = intArrayOf(
            EGL14.EGL_RED_SIZE, 8, EGL14.EGL_GREEN_SIZE, 8,
            EGL14.EGL_BLUE_SIZE, 8, EGL14.EGL_ALPHA_SIZE, 8,
            EGL14.EGL_RENDERABLE_TYPE, EGL14.EGL_SURFACE_TYPE, EGL14.EGL_PBUFFER_BIT,
            EGL14.EGL_BUFFER_SIZE, 32, EGL14.EGL_NONE
        )
        val configs = arrayOfNulls<EGLConfig>(1)
        val counts = IntArray(1)
        if (!EGL14.eglChooseConfig(display, attribs, 0, configs, 0, 1, counts, 0) || counts[0] <= 0) {
            // 不带 EGL_RENDERABLE_TYPE 再试一次：部分设备对 ES2 标记挑剔。
            val fallback = intArrayOf(
                EGL14.EGL_RED_SIZE, 8, EGL14.EGL_GREEN_SIZE, 8, EGL14.EGL_BLUE_SIZE, 8,
                EGL14.EGL_ALPHA_SIZE, 8, EGL14.EGL_SURFACE_TYPE, EGL14.EGL_PBUFFER_BIT, EGL14.EGL_NONE
            )
            if (!EGL14.eglChooseConfig(display, fallback, 0, configs, 0, 1, counts, 0) || counts[0] <= 0) {
                return "eglChooseConfig_failed"
            }
        }
        val chosen = configs[0] ?: return "eglChooseConfig_null"
        config = chosen
        val contextAttribs = intArrayOf(EGL14.EGL_CONTEXT_CLIENT_VERSION, 2, EGL14.EGL_NONE)
        context = EGL14.eglCreateContext(display, chosen, EGL14.EGL_NO_CONTEXT, contextAttribs, 0)
        if (context === EGL14.EGL_NO_CONTEXT) return "eglCreateContext_failed"
        val surfaceAttribs = intArrayOf(EGL14.EGL_WIDTH, 1, EGL14.EGL_HEIGHT, 1, EGL14.EGL_NONE)
        surface = EGL14.eglCreatePbufferSurface(display, chosen, surfaceAttribs, 0)
        if (surface === EGL14.EGL_NO_SURFACE) return "eglCreatePbufferSurface_failed"
        if (!EGL14.eglMakeCurrent(display, surface, surface, context)) return "eglMakeCurrent_failed"
        val ids = IntArray(1)
        GLES20.glGenTextures(1, ids, 0)
        val error = GLES20.glGetError()
        if (ids[0] == 0) return "glGenTextures_failed:glError=$error"
        textureId = ids[0]
        GLES20.glBindTexture(GL_TEXTURE_EXTERNAL_OES, textureId)
        GLES20.glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR)
        GLES20.glTexParameteri(GL_TEXTURE_EXTERNAL_OES, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR)
        GLES20.glBindTexture(GL_TEXTURE_EXTERNAL_OES, 0)
        return null
    }

    fun makeCurrent(): Boolean =
        display !== EGL14.EGL_NO_DISPLAY && EGL14.eglMakeCurrent(display, surface, surface, context)

    fun destroy() {
        if (display !== EGL14.EGL_NO_DISPLAY) {
            if (textureId != 0) GLES20.glDeleteTextures(1, intArrayOf(textureId), 0)
            textureId = 0
            EGL14.eglMakeCurrent(display, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_SURFACE, EGL14.EGL_NO_CONTEXT)
            if (surface !== EGL14.EGL_NO_SURFACE) EGL14.eglDestroySurface(display, surface)
            if (context !== EGL14.EGL_NO_CONTEXT) EGL14.eglDestroyContext(display, context)
            EGL14.eglTerminate(display)
        }
        surface = EGL14.EGL_NO_SURFACE
        context = EGL14.EGL_NO_CONTEXT
        display = EGL14.EGL_NO_DISPLAY
        config = null
    }

    private companion object {
        /** GLES20 常量表里没有 GL_TEXTURE_EXTERNAL_OES，ARCore 要求的正是这个 target。 */
        const val GL_TEXTURE_EXTERNAL_OES = 0x8D65
    }
}
