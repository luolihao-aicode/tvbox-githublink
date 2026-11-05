package com.luolihao.smarttv

import android.content.Intent
import android.view.KeyEvent
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class TvPlaybackTest {
    @Test fun frequentBufferingCannotStopTheOnlyPlayableSource() {
        instrumentation.runOnMainSync{activity.startDiagnostic()}
        waitFor{activity.engine.firstFrameMs!=null}
        val method=PlaybackEngine::class.java.getDeclaredMethod("recover",String::class.java,Boolean::class.javaPrimitiveType,Boolean::class.javaPrimitiveType).apply{isAccessible=true}
        instrumentation.runOnMainSync {
            repeat(4){method.invoke(activity.engine,"frequent_buffering",false,false)}
            repeat(4){method.invoke(activity.engine,"continuous_buffering",false,false)}
            assertEquals("PLAYING",activity.engine.state)
            assertEquals("diagnostic-good",activity.engine.source?.id)
            assertEquals(1,activity.engine.activePlayerCount)
        }
    }
    @Test fun liveWindowErrorRejoinsWithoutReplacingMediaOrPlayer() {
        instrumentation.runOnMainSync{activity.startDiagnostic()}
        waitFor{activity.engine.firstFrameMs!=null}
        val field=PlaybackEngine::class.java.getDeclaredField("player").apply{isAccessible=true}
        val file=java.io.File(instrumentation.targetContext.getExternalFilesDir(android.os.Environment.DIRECTORY_DOCUMENTS),"diagnostics/playback.log")
        val before=file.readText().lines().count{it.contains(" prepare ")}
        var original:Any?=null
        instrumentation.runOnMainSync {
            original=field.get(activity.engine)
            // 真实播放错误时Media3已经进入IDLE；不能在仍正常出帧的READY播放器上只伪造回调。
            (original as androidx.media3.exoplayer.ExoPlayer).stop()
            assertEquals(androidx.media3.common.Player.STATE_IDLE,(original as androidx.media3.exoplayer.ExoPlayer).playbackState)
            engineListener().onPlayerError(androidx.media3.common.PlaybackException("controlled live window failure",null,androidx.media3.common.PlaybackException.ERROR_CODE_BEHIND_LIVE_WINDOW))
        }
        waitFor(10000){activity.engine.state=="PLAYING" && file.readText().contains(" live_rejoin ")}
        instrumentation.runOnMainSync{assertSame(original,field.get(activity.engine));assertEquals(1,activity.engine.activePlayerCount)}
        assertEquals(before,file.readText().lines().count{it.contains(" prepare ")})
    }
    private fun engineListener():androidx.media3.common.Player.Listener {
        val field=PlaybackEngine::class.java.getDeclaredField("listener");field.isAccessible=true
        return field.get(activity.engine) as androidx.media3.common.Player.Listener
    }
    @Test fun idleCallbackCannotReplaceExhaustedStateWithMisleadingStandby() {
        instrumentation.runOnMainSync{activity.startDiagnostic("all-failed")}
        waitFor(30000){activity.engine.state=="EXHAUSTED"}
        instrumentation.runOnMainSync {
            engineListener().onPlaybackStateChanged(androidx.media3.common.Player.STATE_IDLE)
            assertEquals("EXHAUSTED",activity.engine.state)
        }
    }
    @Test fun bufferingCallbacksAloneDoNotReprepareOrRecreateThePlayer() {
        instrumentation.runOnMainSync{activity.startDiagnostic()}
        waitFor{activity.engine.firstFrameMs!=null}
        val field=PlaybackEngine::class.java.getDeclaredField("player").apply{isAccessible=true}
        instrumentation.runOnMainSync {
            val before=field.get(activity.engine)
            val source=activity.engine.source?.id
            repeat(5) {
                engineListener().onPlaybackStateChanged(androidx.media3.common.Player.STATE_BUFFERING)
                engineListener().onPlaybackStateChanged(androidx.media3.common.Player.STATE_READY)
            }
            assertSame(before,field.get(activity.engine));assertEquals(source,activity.engine.source?.id)
            assertEquals("PLAYING",activity.engine.state)
        }
    }
    @Test fun silentHttpPrimarySwitchesToVideoBackupWithinOneStartupTimeout() {
        val server=java.net.ServerSocket(0,1,java.net.InetAddress.getByName("127.0.0.1"))
        val release=java.util.concurrent.CountDownLatch(1)
        val worker=kotlin.concurrent.thread(isDaemon=true) {
            try{server.accept().use{release.await(30,java.util.concurrent.TimeUnit.SECONDS)}}catch(_:Exception){}
        }
        try {
            instrumentation.runOnMainSync {
                val dead=Source("silent-http-primary","http://127.0.0.1:${server.localPort}/live.m3u8","hls",100.0)
                val good=Source("silent-http-backup","asset:///diagnostic.mp4","progressive",90.0)
                for(source in listOf(dead,good))LocalStore(activity).updateQuality("${activity.engine.network}:${source.id}"){Quality()}
                activity.engine.play(Channel("silent-http-test","静默网络恢复测试","诊断","",listOf(dead,good)))
            }
            waitFor(20000){activity.engine.source?.id=="silent-http-backup" && activity.engine.firstFrameMs!=null}
            instrumentation.runOnMainSync{assertEquals(1,activity.engine.activePlayerCount)}
        } finally {release.countDown();server.close();worker.join(1000)}
    }
    private val instrumentation=InstrumentationRegistry.getInstrumentation()
    private lateinit var activity:MainActivity
    @Before fun launch() {
        val intent=Intent(instrumentation.targetContext,MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        activity=instrumentation.startActivitySync(intent) as MainActivity
        instrumentation.waitForIdleSync()
    }
    @After fun close(){instrumentation.runOnMainSync{activity.finish()};instrumentation.waitForIdleSync()}
    private fun waitFor(timeout:Long=20000,condition:()->Boolean) {
        val deadline=System.currentTimeMillis()+timeout
        while(System.currentTimeMillis()<deadline) {
            var done=false;instrumentation.runOnMainSync{done=condition()}
            if(done)return
            Thread.sleep(100)
        }
        var snapshot="";instrumentation.runOnMainSync{snapshot=activity.engine.diagnostics()}
        fail("等待条件超时：$snapshot")
    }
    @Test fun selfMadeOfflineMediaActuallyRendersAndAdvances() {
        instrumentation.runOnMainSync{activity.startDiagnostic()}
        waitFor{activity.engine.firstFrameMs!=null && activity.engine.positionMs>1000}
        instrumentation.runOnMainSync {
            assertEquals("PLAYING",activity.engine.state)
            assertEquals(1,activity.engine.activePlayerCount)
            assertFalse(activity.panelVisible)
            assertEquals("diagnostic-good",activity.engine.source?.id)
        }
    }
    @Test fun corruptedMediaAutomaticallySwitchesToRenderableBackup() {
        instrumentation.runOnMainSync{activity.startDiagnostic("fallback")}
        waitFor(30000){activity.engine.source?.id=="diagnostic-good" && activity.engine.firstFrameMs!=null}
        instrumentation.runOnMainSync{assertEquals(1,activity.engine.activePlayerCount);assertTrue(activity.engine.lastReason.startsWith("player_error"))}
    }
    @Test fun allFailedSourcesStopWithFiniteRecoveryBudget() {
        instrumentation.runOnMainSync{activity.startDiagnostic("all-failed")}
        waitFor(30000){activity.engine.state=="EXHAUSTED"}
        instrumentation.runOnMainSync{assertEquals(1,activity.engine.activePlayerCount)}
    }
    @Test fun remoteOkBackAndDirectionalKeysReachMenusWithoutTouch() {
        instrumentation.runOnMainSync{activity.startDiagnostic()}
        waitFor{activity.engine.firstFrameMs!=null}
        instrumentation.sendKeyDownUpSync(KeyEvent.KEYCODE_DPAD_CENTER)
        instrumentation.waitForIdleSync()
        instrumentation.runOnMainSync{assertTrue(activity.panelVisible);assertNotNull(activity.currentFocus);assertTrue(activity.currentFocus!!.isFocusable)}
        instrumentation.sendKeyDownUpSync(KeyEvent.KEYCODE_DPAD_RIGHT)
        instrumentation.sendKeyDownUpSync(KeyEvent.KEYCODE_DPAD_LEFT)
        instrumentation.sendKeyDownUpSync(KeyEvent.KEYCODE_BACK)
        instrumentation.waitForIdleSync()
        instrumentation.runOnMainSync{assertFalse(activity.panelVisible);assertEquals(1,activity.engine.activePlayerCount)}
    }
    @Test fun rapidChannelSelectionKeepsExactlyOnePlayerAndReleasesOnStop() {
        instrumentation.runOnMainSync{repeat(8){activity.startDiagnostic(if(it%2==0)"fallback" else "normal")};assertEquals(1,activity.engine.activePlayerCount)}
        waitFor{activity.engine.firstFrameMs!=null}
        instrumentation.runOnMainSync{activity.engine.stop();assertEquals(0,activity.engine.activePlayerCount)}
    }
    @Test fun diagnosticLogsAreWrittenToExternalDocuments() {
        instrumentation.runOnMainSync{activity.startDiagnostic()}
        waitFor{activity.engine.firstFrameMs!=null}
        val directory=java.io.File(instrumentation.targetContext.getExternalFilesDir(android.os.Environment.DIRECTORY_DOCUMENTS),"diagnostics")
        assertTrue(java.io.File(directory,"playback.log").readText().contains("first_frame"))
    }
    @Test fun networkCallbacksCannotRestartPlayerAfterActivityStops() {
        instrumentation.runOnMainSync {
            activity.startDiagnostic()
            activity.engine.stop()
            activity.engine.onNetworkChanged(false)
            activity.engine.onNetworkChanged(true)
            assertEquals(0,activity.engine.activePlayerCount)
        }
    }
    @Test fun audioOnlyTelevisionSourceIsRejectedAndVideoBackupRenders() {
        instrumentation.runOnMainSync{activity.startDiagnostic("audio-only")}
        waitFor{activity.engine.source?.id=="diagnostic-good" && activity.engine.firstFrameMs!=null}
        instrumentation.runOnMainSync{assertEquals("audio_only_source",activity.engine.lastReason);assertEquals(1,activity.engine.activePlayerCount)}
    }
}
