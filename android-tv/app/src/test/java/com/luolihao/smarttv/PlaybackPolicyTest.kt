package com.luolihao.smarttv

import org.junit.Assert.*
import org.junit.Test

class PlaybackPolicyTest {
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
