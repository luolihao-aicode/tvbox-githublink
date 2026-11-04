package com.luolihao.smarttv

import android.content.Context
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.media3.common.MediaItem
import androidx.media3.common.MimeTypes
import androidx.media3.common.PlaybackException
import androidx.media3.common.Player
import androidx.media3.common.Tracks
import androidx.media3.common.util.UnstableApi
import androidx.media3.exoplayer.DefaultLoadControl
import androidx.media3.exoplayer.DefaultRenderersFactory
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.datasource.DataSource
import androidx.media3.datasource.DataSpec
import androidx.media3.datasource.TransferListener
import androidx.media3.ui.PlayerView
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

@androidx.annotation.OptIn(UnstableApi::class)
class PlaybackEngine(private val context:Context,private val view:PlayerView,private val store:LocalStore,val log:DiagnosticLog,private val onStatus:(String)->Unit) {
    private val handler=Handler(Looper.getMainLooper())
    private val worker=Executors.newSingleThreadExecutor()
    private val config=PolicyConfig()
    private val monitor=PlaybackHealthMonitor(config)
    private var progress=PlaybackAttemptProgress()
    private class HttpAttempt(val sourceId:String,val number:Int,val progress:NetworkAttemptProgress)
    private class HttpTransfer(val attempt:HttpAttempt,val startedAt:Long,val url:String)
    @Volatile private var httpAttempt=HttpAttempt("none",0,NetworkAttemptProgress(0))
    private val transfers=java.util.IdentityHashMap<DataSource,HttpTransfer>()
    private val transferListener=object:TransferListener {
        override fun onTransferInitializing(source:DataSource,dataSpec:DataSpec,isNetwork:Boolean) {
            if(!isNetwork)return
            val transfer=HttpTransfer(httpAttempt,SystemClock.elapsedRealtime(),redactUrl(dataSpec.uri.toString()))
            synchronized(transfers){transfers[source]=transfer}
            log.event("http_transfer_initializing","source=${transfer.attempt.sourceId} attempt=${transfer.attempt.number} url=${transfer.url}")
        }
        override fun onTransferStart(source:DataSource,dataSpec:DataSpec,isNetwork:Boolean) {
            val transfer=synchronized(transfers){transfers[source]}?:return
            val now=SystemClock.elapsedRealtime()
            transfer.attempt.progress.transferStarted(now)
            log.event("http_transfer_start","source=${transfer.attempt.sourceId} attempt=${transfer.attempt.number} url=${transfer.url} elapsed_ms=${now-transfer.startedAt}")
        }
        override fun onBytesTransferred(source:DataSource,dataSpec:DataSpec,isNetwork:Boolean,bytesTransferred:Int) {
            val transfer=synchronized(transfers){transfers[source]}?:return
            transfer.attempt.progress.bytesTransferred(bytesTransferred)
        }
        override fun onTransferEnd(source:DataSource,dataSpec:DataSpec,isNetwork:Boolean) {
            val transfer=synchronized(transfers){transfers.remove(source)}?:return
            log.event("http_transfer_end","source=${transfer.attempt.sourceId} attempt=${transfer.attempt.number} url=${transfer.url} elapsed_ms=${SystemClock.elapsedRealtime()-transfer.startedAt} attempt_bytes=${transfer.attempt.progress.transferredBytes}")
        }
    }
    private var policy:RecoveryPolicy?=null
    private var player:ExoPlayer?=null
    var channel:Channel?=null;private set
    var source:Source?=null;private set
    var state="IDLE";private set
    var lastReason="无";private set
    var lastError="无";private set
    private var lastErrorDetail="无"
    var network="unknown";private set
    var firstFrameMs:Long?=null;private set
    val activePlayerCount:Int get()=if(player==null)0 else 1
    val positionMs:Long get()=player?.currentPosition?:0
    var firstFrames=0;private set
    var actualFrameRate:Double?=null;private set
    @Volatile private var epoch=0
    @Volatile private var closed=false
    private var suspended=false
    private var recovering=false
    private var tracksKnown=false
    private var hasVideo=true
    private var lastPosition=0L
    private var watchedAt=0L
    private var sessionFirstFrame=false
    private var stable=true
    private var networkLost=false
    private var preflightEpoch=-1
    private var fpsAt=0L
    private var fpsFrames=0
    private val connectivity=context.getSystemService(ConnectivityManager::class.java)
    private val callback:ConnectivityManager.NetworkCallback=object:ConnectivityManager.NetworkCallback() {
        override fun onLost(network:Network) {handler.post{onNetworkChanged(false)}}
        override fun onAvailable(network:Network) {handler.post{onNetworkChanged(true)}}
    }
    internal fun onNetworkChanged(available:Boolean) {
        if(closed)return
        if(!available){networkLost=true;log.event("network_lost");if(!suspended)onStatus("网络连接中断，等待恢复")}
        else {
            val old=this@PlaybackEngine.network
            this@PlaybackEngine.network=networkKey()
            log.event("network_available",this@PlaybackEngine.network)
            if(!suspended && (networkLost || (old!="unknown" && old!=this@PlaybackEngine.network))) {
                networkLost=false
                channel?.let{play(it)}
            }
        }
    }
    fun play(selected:Channel) {
        check(Looper.myLooper()==Looper.getMainLooper())
        if(closed)return
        suspended=false
        flushWatch();epoch++;recovering=false;preflightEpoch=-1;lastReason="用户选台";lastError="无";lastErrorDetail="无"
        channel=selected;network=networkKey();stable=store.stableBuffer
        val ranked=SourceSelector.rank(selected.sources,store.qualities(),network)
        policy=RecoveryPolicy(ranked,config);progress=PlaybackAttemptProgress();store.viewed(selected.id)
        log.event("channel_selected","channel=${selected.id} candidates=${ranked.size} network=$network")
        if(ranked.isEmpty()){state="EXHAUSTED";onStatus("此频道没有可用来源");return}
        prepare(ranked.first(),rebuild=true)
    }
    private fun makePlayer():ExoPlayer {
        // 一个活跃播放器；备用检查仅HTTP请求，永不创建第二个解码器。
        val load=DefaultLoadControl.Builder().setBufferDurationsMs(if(stable)20000 else 10000,if(stable)45000 else 25000,1500,if(stable)5000 else 3000).setTargetBufferBytes(24*1024*1024).setPrioritizeTimeOverSizeThresholds(false).build()
        val http=DefaultHttpDataSource.Factory().setConnectTimeoutMs(7000).setReadTimeoutMs(7000).setAllowCrossProtocolRedirects(false).setUserAgent("SmartLiveTV/1.0").setTransferListener(transferListener)
        return ExoPlayer.Builder(context,DefaultRenderersFactory(context).setEnableDecoderFallback(true))
            .setLoadControl(load).setMediaSourceFactory(DefaultMediaSourceFactory(DefaultDataSource.Factory(context,http))).build().apply {
                setWakeMode(androidx.media3.common.C.WAKE_MODE_NETWORK)
                addListener(listener)
            }
    }
    private val listener:Player.Listener=object:Player.Listener {
        override fun onPlaybackStateChanged(playbackState:Int) {
            if(recovering || closed)return
            state=when(playbackState){Player.STATE_BUFFERING->if(sessionFirstFrame)"BUFFERING" else "PREPARING";Player.STATE_READY->if(sessionFirstFrame || !hasVideo)"PLAYING" else "PREPARING";Player.STATE_ENDED->"ENDED";else->"IDLE"}
            if(playbackState==Player.STATE_BUFFERING && sessionFirstFrame) updateQuality{it.copy(stalls=it.stalls+1)}
            if(playbackState==Player.STATE_ENDED)recover("stream_ended")
            onStatus("${channel?.name?:""} · ${stateLabel()}")
        }
        override fun onTracksChanged(tracks:Tracks) {
            tracksKnown=!tracks.isEmpty
            hasVideo=tracks.groups.any{it.type==androidx.media3.common.C.TRACK_TYPE_VIDEO}
            log.event("tracks","video=$hasVideo groups=${tracks.groups.size}")
            if(tracksKnown && !hasVideo && !recovering)recover("audio_only_source",fatalDecode=true)
        }
        override fun onRenderedFirstFrame() {
            if(recovering || closed)return
            val now=SystemClock.elapsedRealtime()
            monitor.firstFrame(now);progress.firstFrame(now);firstFrameMs=monitor.firstFrameMs;firstFrames++
            if(!sessionFirstFrame) {
                sessionFirstFrame=true;watchedAt=SystemClock.elapsedRealtime()
                updateQuality{it.copy(successes=it.successes+1,firstFrameMs=firstFrameMs?:0,lastPlayed=System.currentTimeMillis())}
            }
            state="PLAYING";log.event("first_frame","source=${source?.id} ms=$firstFrameMs reason=$lastReason ${progressDetail(now)}")
            onStatus("${channel?.name} · 播放中")
            preflightBackup()
        }
        override fun onPlayerError(error:PlaybackException) {
            if(recovering || closed)return
            lastError=error.errorCodeName
            val http=generateSequence<Throwable>(error){it.cause}.take(6).filterIsInstance<androidx.media3.datasource.HttpDataSource.InvalidResponseCodeException>().firstOrNull()?.responseCode
            lastErrorDetail=diagnosticErrorTypes(error)+(http?.let{" HTTP $it"}?:"")
            log.event("player_error","code=${error.errorCodeName} source=${source?.id} causes=$lastErrorDetail")
            recover("player_error_${error.errorCodeName}",error.errorCode in 4000..4999 || error.errorCode in 3000..3999)
        }
    }
    private fun prepare(selected:Source,rebuild:Boolean) {
        flushWatch()
        if(rebuild) {player?.removeListener(listener);view.player=null;player?.release();player=null;player=makePlayer();view.player=player}
        source=selected;sessionFirstFrame=false;firstFrameMs=null;tracksKnown=false;hasVideo=true;lastPosition=0;actualFrameRate=null;fpsAt=0;fpsFrames=0
        val now=SystemClock.elapsedRealtime()
        monitor.begin(now);progress.begin(now,selected.id);state="PREPARING";recovering=false
        httpAttempt=HttpAttempt(selected.id,progress.attempts,NetworkAttemptProgress(now))
        synchronized(transfers){transfers.clear()}
        val mime=when(selected.type){"hls"->MimeTypes.APPLICATION_M3U8;"dash"->MimeTypes.APPLICATION_MPD;else->MimeTypes.VIDEO_MP4}
        player!!.setMediaItem(MediaItem.Builder().setUri(selected.url).setMimeType(mime).build())
        player!!.repeatMode=if(selected.url=="asset:///diagnostic.mp4")Player.REPEAT_MODE_ONE else Player.REPEAT_MODE_OFF
        player!!.prepare();player!!.playWhenReady=true
        log.event("prepare","source=${selected.id} url=${redactUrl(selected.url)} rebuild=$rebuild buffer=${if(stable)"stable" else "balanced"} ${progressDetail(now)}")
        onStatus("${channel?.name} · 正在载入")
    }
    private fun recover(reason:String,fatalDecode:Boolean=false) {
        if(recovering || closed || state=="EXHAUSTED")return
        // 短时间反复缓冲表明该线路不适合本机网络，有备源时直接换源。
        val now=SystemClock.elapsedRealtime()
        val action=policy?.failure(now,fatalDecode || reason=="frequent_buffering",progress.shouldPreferBackup(reason))?:return
        if(action.kind==ActionKind.WAIT) {
            recovering=true;val token=epoch
            handler.postDelayed({if(token==epoch && !closed){recovering=false;recover(reason,fatalDecode)}},action.delayMs);return
        }
        progress.failed(now)
        flushWatch();lastReason=reason;lastError=if(lastError=="无")reason else lastError
        updateQuality{it.copy(failures=it.failures+1,switches=it.switches+if(action.kind==ActionKind.SWITCH)1 else 0,lastPlayed=System.currentTimeMillis())}
        stable=true;state="RECOVERING";recovering=true;player?.stop()
        log.event("recovery","reason=$reason action=${action.kind} next=${action.sourceId} delay=${action.delayMs} ${progressDetail(now)}")
        if(action.kind==ActionKind.EXHAUSTED){state="EXHAUSTED";recovering=false;onStatus("所有来源均不可用。请换台或稍后从频道列表重试");return}
        onStatus("播放恢复中：${when(action.kind){ActionKind.RETRY->"重试当前源";ActionKind.REBUILD->"重建解码会话";else->"切换备用源"}}")
        val token=epoch
        handler.postDelayed({
            if(token==epoch && !closed) {
                val next=policy!!.sources.first{it.id==action.sourceId}
                prepare(next,action.kind!=ActionKind.RETRY)
            }
        },action.delayMs)
    }
    private val tick:Runnable=object:Runnable {
        override fun run() {
            val p=player
            if(!closed && !suspended && p!=null && !recovering && state!="EXHAUSTED") {
                val pos=p.currentPosition
                val counters=p.videoDecoderCounters
                val frames=counters?.renderedOutputBufferCount?:0
                val now=SystemClock.elapsedRealtime()
                if(fpsAt==0L){fpsAt=now;fpsFrames=frames}
                else if(now-fpsAt>=3000){actualFrameRate=if(p.isPlaying && hasVideo)(frames-fpsFrames).coerceAtLeast(0)*1000.0/(now-fpsAt) else null;fpsAt=now;fpsFrames=frames}
                val reason=monitor.observe(HealthSample(SystemClock.elapsedRealtime(),p.playbackState==Player.STATE_READY,p.playbackState==Player.STATE_BUFFERING,!tracksKnown || hasVideo,pos>lastPosition,pos,counters?.renderedOutputBufferCount?:0,!p.playWhenReady))
                lastPosition=pos
                if(reason!=null)recover(reason)
                if(watchedAt>0 && SystemClock.elapsedRealtime()-watchedAt>15000)flushWatch()
            }
            if(!closed)handler.postDelayed(this,1000)
        }
    }
    init {
        network=networkKey()
        try {
            if(android.os.Build.VERSION.SDK_INT>=24)connectivity.registerDefaultNetworkCallback(callback)
            else connectivity.registerNetworkCallback(android.net.NetworkRequest.Builder().addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET).build(),callback)
        }catch(_:Exception){}
        handler.post(tick)
    }
    private fun updateQuality(change:(Quality)->Quality){source?.let{store.updateQuality("$network:${it.id}",change)}}
    private fun flushWatch() {
        if(watchedAt<=0)return
        val now=SystemClock.elapsedRealtime();val delta=now-watchedAt
        if(player?.isPlaying==true)updateQuality{it.copy(watchedMs=it.watchedMs+delta,lastPlayed=System.currentTimeMillis())}
        watchedAt=if(sessionFirstFrame)now else 0
    }
    private fun preflightBackup() {
        if(!store.preflight || preflightEpoch==epoch)return
        val candidate=policy?.sources?.firstOrNull{it.id!=source?.id && it.url.startsWith("https://")}?:return
        preflightEpoch=epoch;val token=epoch
        worker.execute {
            if(token!=epoch || closed)return@execute
            val start=SystemClock.elapsedRealtime()
            val code=try {
                val c=(URL(candidate.url).openConnection() as HttpURLConnection).apply{requestMethod="HEAD";connectTimeout=2500;readTimeout=2500;instanceFollowRedirects=false}
                try{c.responseCode}finally{c.disconnect()}
            }catch(_:Exception){-1}
            handler.post{if(token==epoch && !closed)log.event("backup_http_preflight","source=${candidate.id} http=$code elapsed=${SystemClock.elapsedRealtime()-start} mediaValidated=false")}
        }
    }
    fun diagnostics():String {
        val p=player;val f=p?.videoFormat
        val q=source?.let{store.qualities()["$network:${it.id}"]}
        return """当前频道：${channel?.name?:"未选择"}
来源：${source?.id?:"无"} ${source?.url?.let(::redactUrl)?:""}
媒体协议：${source?.type?:"未知"}
状态：${stateLabel()}（$state）
分辨率：${f?.width?:"未知"} × ${f?.height?:"未知"}
编码：${f?.codecs?:f?.sampleMimeType?:"未知"}
媒体标称帧率：${f?.frameRate?.takeIf{it>0}?:"未知"}（非实测输出帧率）
实测输出帧率：${actualFrameRate?.let{String.format(java.util.Locale.ROOT,"%.1f fps",it)}?:"等待采样"}（3秒窗口）
已渲染帧：${p?.videoDecoderCounters?.renderedOutputBufferCount?:0}
缓冲：${p?.totalBufferedDuration?:0}ms；策略：${if(stable)"稳定优先 20–45秒上限" else "均衡 10–25秒上限"}
首帧：${firstFrameMs?.let{"${it}ms"}?:"未出现"}
服务端评分：${source?.score?:"未知"}；备用数：${((policy?.sources?.size?:0)-1).coerceAtLeast(0)}
最近切源/恢复原因：$lastReason
最近错误：$lastError
本机网络：$network；观看：${(q?.watchedMs?:0)/1000}秒；卡顿：${q?.stalls?:0}；异常：${q?.failures?:0}
播放器实例：$activePlayerCount；备用HTTP预检：${if(store.preflight)"开启（仅连通性，非媒体预缓冲）" else "关闭"}
日志目录：${log.directory.absolutePath}"""
    }
    fun photoDiagnostics():String {
        val p=player;val f=p?.videoFormat
        return """频道：${channel?.name?:"未选择"}    状态：$state
线路：${(policy?.index?:0)+1}/${policy?.sources?.size?:0}  ${source?.url?.let(::redactUrl)?:"无"}
首帧：${firstFrameMs?.let{"${it}ms"}?:"未出现"}    已渲染帧：${p?.videoDecoderCounters?.renderedOutputBufferCount?:0}
尝试：${progress.attempts}次  失败：${progress.failures}次  首帧累计等待：${progress.firstFrameWaitMs(SystemClock.elapsedRealtime())/1000}秒
画面：${f?.width?:0}×${f?.height?:0}  ${f?.sampleMimeType?:"未识别视频"}  ${source?.type?:"未知协议"}
缓存：${p?.totalBufferedDuration?:0}ms  收到：${httpAttempt.progress.transferredBytes}B  首传输：${httpAttempt.progress.firstTransferMs?.let{"${it}ms"}?:"未开始"}
恢复原因：$lastReason
错误码：$lastError
失败类型：$lastErrorDetail
本机网络：$network
本页不上传日志，也不包含访问令牌。"""
    }
    private fun progressDetail(now:Long)="attempt=${progress.attempts} failures=${progress.failures} first_frame_wait_ms=${progress.firstFrameWaitMs(now)} http_bytes=${httpAttempt.progress.transferredBytes} first_transfer_ms=${httpAttempt.progress.firstTransferMs}"
    private fun stateLabel()=when(state){"PLAYING"->"播放中";"PREPARING"->"等待首帧";"BUFFERING"->"缓冲中";"RECOVERING"->"恢复中";"EXHAUSTED"->"来源耗尽";else->"待机"}
    private fun networkKey():String {
        val n=connectivity.activeNetwork?:return "offline"
        val c=connectivity.getNetworkCapabilities(n)?:return "unknown"
        val transport=when{c.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)->"ethernet";c.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)->"wifi";c.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)->"cellular";else->"other"}
        val route=connectivity.getLinkProperties(n)?.routes?.firstOrNull{it.isDefaultRoute}?.gateway?.hostAddress?:"unknown"
        return "$transport:${sha256(route).take(8)}"
    }
    fun stop() {suspended=true;progress.finish(SystemClock.elapsedRealtime());flushWatch();epoch++;player?.removeListener(listener);view.player=null;player?.release();player=null;state="IDLE";watchedAt=0;sessionFirstFrame=false;recovering=false;log.event("player_released")}
    fun close(){closed=true;stop();handler.removeCallbacksAndMessages(null);try{connectivity.unregisterNetworkCallback(callback)}catch(_:Exception){};worker.shutdownNow()}
}
