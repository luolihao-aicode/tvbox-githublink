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
