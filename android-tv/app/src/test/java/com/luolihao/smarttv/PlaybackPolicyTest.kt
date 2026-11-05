package com.luolihao.smarttv

import org.junit.Assert.*
import org.junit.Test

class PlaybackPolicyTest {
    @Test fun exhaustedBackupsReturnToPreviouslyRenderedSourceWithBackoff() {
        val p=RecoveryPolicy(listOf(source("a"),source("b")))
        p.rendered("a")
        assertTrue(p.keepBuffering("continuous_buffering","a"))
        assertFalse(p.keepBuffering("continuous_buffering","b"))
        assertFalse(p.keepBuffering("player_error_ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT","a"))
        assertEquals(ActionKind.SWITCH,p.failure(0,fatalDecode=true).kind)
        val fallback=p.failure(3000,fatalDecode=true)
        assertEquals(ActionKind.RETRY,fallback.kind)
        assertEquals("a",fallback.sourceId)
        assertTrue(fallback.delayMs>=30000)
        assertEquals(0,p.index)
        for(now in listOf(70000L,140000L,210000L,280000L)) {
            assertNotEquals(ActionKind.EXHAUSTED,p.failure(now,fatalDecode=true).kind)
        }
    }
    @Test fun downloadSpeedUsesElapsedTimeAndClearsOnSourceChange() {
        val speed=DownloadSpeedSampler()
        assertEquals(0L,speed.sample(1000,100,"a"))
        assertEquals(2048L,speed.sample(3000,4196,"a"))
        assertEquals(0L,speed.sample(4000,4196,"a"))
        assertEquals(0L,speed.sample(5000,20,"b"))
        assertEquals(1024L,speed.sample(6000,1044,"b"))
    }
    @Test fun shortRecoverableStallsMustNotStopEvenTheLastPlayableSource() {
        assertFalse(shouldRecoverHealth("frequent_buffering"))
        assertTrue(shouldRecoverHealth("continuous_buffering"))
        assertTrue(shouldRecoverHealth("video_progress_stalled"))
        assertTrue(shouldRecoverHealth("first_frame_timeout"))
    }
    @Test fun frequentStallWarningCannotMaskARealContinuousTimeout() {
        val h=PlaybackHealthMonitor();h.begin(0);h.firstFrame(100)
        for(time in listOf(1000L,3000L)) {
            h.observe(HealthSample(time,buffering=true))
            h.observe(HealthSample(time+500,ready=true,positionMs=time,renderedFrames=time.toInt()))
        }
        assertEquals("frequent_buffering",h.observe(HealthSample(5000,buffering=true)))
        assertEquals("continuous_buffering",h.observe(HealthSample(17000,buffering=true)))
    }
    @Test fun liveWindowExpiryRejoinsLiveEdgeWithFiniteBudgetThenUsesBackup() {
        val p=RecoveryPolicy(listOf(source("a"),source("b")))
        assertEquals("REJOIN_LIVE",p.failure(0,liveWindowExpired=true).kind.name)
        assertEquals(ActionKind.WAIT,p.failure(1,liveWindowExpired=true).kind)
        assertEquals("REJOIN_LIVE",p.failure(3000,liveWindowExpired=true).kind.name)
        assertEquals(ActionKind.SWITCH,p.failure(6000,liveWindowExpired=true).kind)
        assertEquals("b",p.sources[p.index].id)
        assertEquals("REJOIN_LIVE",p.failure(9000,liveWindowExpired=true).kind.name)
        assertEquals("REJOIN_LIVE",p.failure(12000,liveWindowExpired=true).kind.name)
        assertEquals(ActionKind.EXHAUSTED,p.failure(15000,liveWindowExpired=true).kind)
    }
    @Test fun photoErrorSummaryIncludesCauseTypesWithoutSecrets() {
        val error=java.io.IOException("https://user:password@media.example/live?token=secret",java.net.SocketTimeoutException("secret"))
        assertEquals("IOException > SocketTimeoutException",diagnosticErrorTypes(error))
    }
    @Test fun backupOrderTriesDifferentOriginBeforeAliasesOfPrimary() {
        val sources=listOf(source("a",99.0).copy(originGroup="shared"),source("alias",98.0).copy(originGroup="shared"),source("other",70.0))
        assertEquals(listOf("a","other","alias"),SourceSelector.rank(sources,emptyMap(),"wifi").map{it.id})
    }
    @Test fun repeatedShortBufferingIsDetectedBeforeContinuousTimeout() {
        val h=PlaybackHealthMonitor();h.begin(0);h.firstFrame(100)
        for(time in listOf(1000L,6000L)) {
            assertNull(h.observe(HealthSample(time,buffering=true)))
            assertNull(h.observe(HealthSample(time+1000,ready=true,positionMs=time,renderedFrames=(time/40).toInt())))
        }
        assertEquals("frequent_buffering",h.observe(HealthSample(11000,buffering=true)))
    }
    @Test fun oldBufferingAndPauseDoNotCountAsFrequentStalls() {
        val h=PlaybackHealthMonitor();h.begin(0);h.firstFrame(100)
        for(time in listOf(1000L,70000L,140000L)) {
            assertNull(h.observe(HealthSample(time,buffering=true)))
            assertNull(h.observe(HealthSample(time+1000,ready=true,positionMs=time,renderedFrames=(time/40).toInt())))
        }
        assertNull(h.observe(HealthSample(141100,paused=true,buffering=true)))
    }
    private fun source(id: String, score: Double = 80.0) = Source(id, "https://example.org/$id.m3u8", "hls", score, originGroup = id)
    @Test fun localSuccessOutweighsServerScoreForCurrentNetwork() {
        val stats = mapOf("wifi:b" to Quality(successes = 5, watchedMs = 120000))
        val sources = listOf(source("a",99.0), source("b",20.0))
        assertEquals("b", SourceSelector.rank(sources,stats,"wifi").first().id)
        assertEquals("a", SourceSelector.rank(sources,stats,"ethernet").first().id)
    }
    @Test fun failuresRetryThenRebuildThenSwitchAndEventuallyStop() {
        val p = RecoveryPolicy(listOf(source("a"),source("b")), PolicyConfig(cooldownMs=100))
        assertEquals(ActionKind.RETRY,p.failure(0).kind)
        assertEquals(ActionKind.REBUILD,p.failure(1000).kind)
        val switch = p.failure(2000)
        assertEquals(ActionKind.SWITCH,switch.kind); assertEquals("b",switch.sourceId)
        assertEquals(ActionKind.WAIT,p.failure(2050).kind)
        assertEquals(ActionKind.RETRY,p.failure(3000).kind)
        assertEquals(ActionKind.REBUILD,p.failure(4000).kind)
        assertEquals(ActionKind.EXHAUSTED,p.failure(5000).kind)
        assertEquals(ActionKind.EXHAUSTED,p.failure(1000000).kind)
    }
    @Test fun singleUnreachableSourceHasFiniteBudget() {
        val p=RecoveryPolicy(listOf(source("a")))
        assertEquals(ActionKind.RETRY,p.failure(0).kind)
        assertEquals(ActionKind.REBUILD,p.failure(5000).kind)
        assertEquals(ActionKind.EXHAUSTED,p.failure(10000).kind)
    }
    @Test fun startupTimeoutShouldTryBackupInsteadOfRetryingTheSameUnreachableSource() {
        val p=RecoveryPolicy(listOf(source("a"),source("b"),source("c")))
        val next=p.failure(12000,preferBackup=shouldPreferBackup("first_frame_timeout",hasFirstFrame=false))
        assertEquals(ActionKind.SWITCH,next.kind)
        assertEquals("b",next.sourceId)
        assertEquals("c",p.failure(24500,preferBackup=true).sourceId)
        assertEquals(ActionKind.EXHAUSTED,p.failure(37000,preferBackup=true).kind)
        assertEquals(ActionKind.EXHAUSTED,p.failure(100000,preferBackup=true).kind)
    }
    @Test fun singleSourceFirstFrameTimeoutRetainsFiniteRetryAndRebuildBudget() {
        val p=RecoveryPolicy(listOf(source("a")))
        assertEquals(ActionKind.RETRY,p.failure(12000,preferBackup=true).kind)
        assertEquals(ActionKind.REBUILD,p.failure(25000,preferBackup=true).kind)
        assertEquals(ActionKind.EXHAUSTED,p.failure(39000,preferBackup=true).kind)
    }
    @Test fun startupTimeoutDoesNotBypassRecoveryCooldown() {
        val p=RecoveryPolicy(listOf(source("a"),source("b")))
        assertEquals(ActionKind.SWITCH,p.failure(12000,preferBackup=true).kind)
        assertEquals(ActionKind.WAIT,p.failure(12500,preferBackup=true).kind)
        assertEquals(ActionKind.EXHAUSTED,p.failure(14000,preferBackup=true).kind)
    }
    @Test fun onlyStartupTimeoutsAndStartupConnectionErrorsPreferBackup() {
        for(reason in listOf("first_frame_timeout","prepare_timeout","player_error_ERROR_CODE_IO_NETWORK_CONNECTION_FAILED","player_error_ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT")) {
            assertTrue(reason,shouldPreferBackup(reason,hasFirstFrame=false))
        }
        for(reason in listOf("player_error_ERROR_CODE_IO_UNSPECIFIED","player_error_ERROR_CODE_IO_BAD_HTTP_STATUS","continuous_buffering","video_progress_stalled")) {
            assertFalse(reason,shouldPreferBackup(reason,hasFirstFrame=false))
        }
        assertFalse(shouldPreferBackup("player_error_ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT",hasFirstFrame=true))
    }
    @Test fun connectionErrorsAfterFirstFrameKeepRetryAndRebuildBudget() {
        val p=RecoveryPolicy(listOf(source("a"),source("b")))
        val prefer=shouldPreferBackup("player_error_ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT",hasFirstFrame=true)
        assertEquals(ActionKind.RETRY,p.failure(12000,preferBackup=prefer).kind)
        assertEquals(ActionKind.REBUILD,p.failure(25000,preferBackup=prefer).kind)
        assertEquals(ActionKind.SWITCH,p.failure(39000,preferBackup=prefer).kind)
    }
    @Test fun retryAndRebuildOfPreviouslyRenderedSourceKeepTheRecoveryBudget() {
        val p=RecoveryPolicy(listOf(source("a"),source("b")))
        val progress=PlaybackAttemptProgress()
        val reason="player_error_ERROR_CODE_IO_NETWORK_CONNECTION_TIMEOUT"
        progress.begin(0,"a");progress.firstFrame(100)
        assertEquals(ActionKind.RETRY,p.failure(12000,preferBackup=progress.shouldPreferBackup(reason)).kind)
        progress.failed(12000);progress.begin(13000,"a")
        assertEquals(ActionKind.REBUILD,p.failure(25000,preferBackup=progress.shouldPreferBackup(reason)).kind)
        progress.failed(25000);progress.begin(27000,"a")
        assertEquals(ActionKind.SWITCH,p.failure(39000,preferBackup=progress.shouldPreferBackup(reason)).kind)
        progress.failed(39000);progress.begin(39500,"b")
        assertTrue(progress.shouldPreferBackup(reason))
    }
    @Test fun diagnosticsAccumulateOnlyFirstFrameWaitAcrossAttemptsAndCountRealFailures() {
        val progress=PlaybackAttemptProgress()
        progress.begin(1000)
        assertEquals(1,progress.attempts)
        assertEquals(3000L,progress.firstFrameWaitMs(4000))
        progress.failed(13000)
        assertEquals(1,progress.failures)
        assertEquals(12000L,progress.firstFrameWaitMs(13500))
        progress.begin(13500)
        progress.firstFrame(15500)
        assertEquals(2,progress.attempts)
        assertEquals(14000L,progress.firstFrameWaitMs(30000))
        progress.failed(30000)
        assertEquals(2,progress.failures)
        assertEquals(14000L,progress.firstFrameWaitMs(31000))
    }
    @Test fun stoppingAnAttemptFreezesDiagnosticWaitWithoutCountingAFailure() {
        val progress=PlaybackAttemptProgress()
        progress.begin(1000);progress.finish(4000)
        assertEquals(3000L,progress.firstFrameWaitMs(10000))
        assertEquals(0,progress.failures)
    }
    @Test fun networkDiagnosticsCountAllReadsAndKeepFirstTransferTimeAcrossSegments() {
        val progress=NetworkAttemptProgress(1000)
        assertNull(progress.firstTransferMs)
        progress.transferStarted(1300);progress.bytesTransferred(100)
        progress.transferStarted(2000);progress.bytesTransferred(250)
        assertEquals(300L,progress.firstTransferMs)
        assertEquals(350L,progress.transferredBytes)
    }
    @Test fun newNetworkAttemptDoesNotInheritPreviousBytesOrResponseTime() {
        val previous=NetworkAttemptProgress(1000)
        previous.transferStarted(1300);previous.bytesTransferred(100)
        val current=NetworkAttemptProgress(2000)
        previous.bytesTransferred(200)
        assertNull(current.firstTransferMs)
        assertEquals(0L,current.transferredBytes)
    }
    @Test fun decoderFailureSkipsDirectlyToBackupAndQuarantinesSource() {
        val p=RecoveryPolicy(listOf(source("a"),source("b")))
        assertEquals("b",p.failure(0,fatalDecode=true).sourceId)
        assertTrue(p.quarantineUntil["a"]!!>0)
    }
    @Test fun readyWithAudioButNoFirstVideoFrameFails() {
        val h=PlaybackHealthMonitor(PolicyConfig(firstFrameTimeoutMs=4000));h.begin(0)
        assertNull(h.observe(HealthSample(1000,ready=true,hasVideo=true,audioAdvancing=true)))
        assertEquals("first_frame_timeout",h.observe(HealthSample(5000,ready=true,hasVideo=true,audioAdvancing=true)))
    }
    @Test fun explicitAudioOnlyStreamDoesNotTriggerBlackScreenRecovery() {
        val h=PlaybackHealthMonitor(PolicyConfig(firstFrameTimeoutMs=1000));h.begin(0)
        assertNull(h.observe(HealthSample(2000,ready=true,hasVideo=false,audioAdvancing=true)))
    }
    @Test fun continuousBufferingAndNoPositionProgressAreDetectedButPauseIsIgnored() {
        val h=PlaybackHealthMonitor(PolicyConfig(bufferTimeoutMs=1000,stallTimeoutMs=2000));h.begin(0);h.firstFrame(10)
        assertNull(h.observe(HealthSample(20,buffering=true)))
        assertEquals("continuous_buffering",h.observe(HealthSample(1200,buffering=true)))
        h.begin(2000);h.firstFrame(2010)
        h.observe(HealthSample(2100,ready=true,positionMs=100,renderedFrames=1))
        assertNull(h.observe(HealthSample(9000,ready=true,positionMs=100,renderedFrames=1,paused=true)))
        assertNull(h.observe(HealthSample(9100,ready=true,positionMs=100,renderedFrames=1)))
        assertEquals("video_progress_stalled",h.observe(HealthSample(12000,ready=true,positionMs=100,renderedFrames=1)))
    }
    @Test fun staticPictureWithRenderingFramesIsNotAFreeze() {
        val h=PlaybackHealthMonitor(PolicyConfig(stallTimeoutMs=1000));h.begin(0);h.firstFrame(1)
        h.observe(HealthSample(2,ready=true,positionMs=1,renderedFrames=1))
        assertNull(h.observe(HealthSample(5000,ready=true,positionMs=5000,renderedFrames=2)))
    }
    @Test fun diagnosticsNeverContainQueryUserinfoOrUrlPathSecrets() {
        assertEquals("https://example.org/…",redactUrl("https://user:pass@example.org/private/token/file.m3u8?token=secret#fragment"))
        val clean=redactMessage("failure https://user:pass@example.org/token/abc?secret=hidden other")
        assertFalse(clean.contains("hidden"));assertFalse(clean.contains("pass"));assertFalse(clean.contains("abc"))
    }
    @Test fun preflightRequestsAreDisabledInLowResourceDefault() { assertFalse(PlaybackPreferences().preflightEnabled) }
}
