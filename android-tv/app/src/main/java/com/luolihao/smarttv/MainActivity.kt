package com.luolihao.smarttv

import android.app.Activity
import android.graphics.Color
import android.graphics.Typeface
import android.graphics.drawable.GradientDrawable
import android.graphics.drawable.StateListDrawable
import android.os.Bundle
import android.view.Gravity
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.*
import android.text.Editable
import android.text.TextWatcher
import androidx.media3.common.util.UnstableApi
import androidx.media3.ui.PlayerView
import java.io.File
import java.util.concurrent.Executors

@androidx.annotation.OptIn(UnstableApi::class)
class MainActivity:Activity() {
    companion object { const val SETTINGS_ID=1001;const val DIAGNOSTICS_ID=1002;const val CHANNEL_LIST_ID=1003;const val CLOSE_ID=1004 }
    private lateinit var root:FrameLayout
    private lateinit var overlay:LinearLayout
    private lateinit var status:TextView
    private lateinit var store:LocalStore
    lateinit var engine:PlaybackEngine;private set
    private lateinit var log:DiagnosticLog
    private lateinit var repository:CatalogRepository
    private val worker=Executors.newSingleThreadExecutor()
    private var catalog:Catalog?=null
    private var loadMessage="正在加载频道目录…"
    private var category="全部"
    private var query=""
    private var page=0
    private var refreshEpoch=0
    private var screen="channels"
    val panelVisible:Boolean get()=::overlay.isInitialized && overlay.visibility==View.VISIBLE
    override fun onCreate(savedInstanceState:Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        @Suppress("DEPRECATION")
        window.decorView.systemUiVisibility=View.SYSTEM_UI_FLAG_FULLSCREEN or View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
        store=LocalStore(this);log=DiagnosticLog(this)
        repository=CatalogRepository(CatalogCache(File(filesDir,"catalog-cache")))
        root=FrameLayout(this).apply{setBackgroundColor(Color.BLACK)}
        val video=PlayerView(this).apply{useController=false;isFocusable=false;keepScreenOn=true}
        root.addView(video,FrameLayout.LayoutParams(-1,-1))
        status=text("智选直播 · 选择频道开始",18).apply{setPadding(dp(24),dp(12),dp(24),dp(12));setBackgroundColor(0xB0000000.toInt());isFocusable=false}
        root.addView(status,FrameLayout.LayoutParams(-1,dp(52),Gravity.BOTTOM))
        overlay=LinearLayout(this).apply{orientation=LinearLayout.VERTICAL;setPadding(dp(26),dp(18),dp(26),dp(18));setBackgroundColor(0xF2111D2B.toInt())}
        root.addView(overlay,FrameLayout.LayoutParams(-1,-1))
        setContentView(root)
        engine=PlaybackEngine(this,video,store,log){status.text=it}
        showChannels();refreshCatalog()
    }
    override fun onStart() {super.onStart();if(::engine.isInitialized && engine.activePlayerCount==0)engine.channel?.let{engine.play(it)}}
    override fun onStop(){if(::engine.isInitialized)engine.stop();super.onStop()}
    override fun onDestroy(){refreshEpoch++;worker.shutdownNow();if(::engine.isInitialized)engine.close();super.onDestroy()}
    private fun dp(value:Int)=(resources.displayMetrics.density*value).toInt()
    private fun text(value:String,size:Int=20)=TextView(this).apply{this.text=value;textSize=size.toFloat();setTextColor(Color.WHITE);setPadding(dp(8),dp(5),dp(8),dp(5))}
    private fun shape(color:Int)=GradientDrawable().apply{setColor(color);cornerRadius=dp(7).toFloat()}
    private fun button(value:String,idValue:Int=View.NO_ID,click:()->Unit)=Button(this).apply {
        id=idValue;text=value;textSize=16f;isAllCaps=false;setTextColor(Color.WHITE);isFocusable=true;isFocusableInTouchMode=true
        background=StateListDrawable().apply{addState(intArrayOf(android.R.attr.state_focused),shape(0xFF087E8B.toInt()));addState(intArrayOf(android.R.attr.state_pressed),shape(0xFF087E8B.toInt()));addState(intArrayOf(),shape(0xFF26384B.toInt()))}
        setPadding(dp(14),dp(6),dp(14),dp(6));minHeight=dp(44);setOnClickListener{click()}
    }
    private fun toolbar(title:String) {
        overlay.removeAllViews();overlay.visibility=View.VISIBLE
        overlay.addView(text(title,26).apply{setTypeface(null,Typeface.BOLD)})
        val row=LinearLayout(this).apply{orientation=LinearLayout.HORIZONTAL}
        row.addView(button("频道"){showChannels()},LinearLayout.LayoutParams(0,dp(48),1f).apply{setMargins(dp(4),dp(4),dp(4),dp(4))})
        row.addView(button("设置",SETTINGS_ID){showSettings()},LinearLayout.LayoutParams(0,dp(48),1f).apply{setMargins(dp(4),dp(4),dp(4),dp(4))})
        row.addView(button("诊断",DIAGNOSTICS_ID){showDiagnostics()},LinearLayout.LayoutParams(0,dp(48),1f).apply{setMargins(dp(4),dp(4),dp(4),dp(4))})
        row.addView(button("返回播放",CLOSE_ID){hidePanel()},LinearLayout.LayoutParams(0,dp(48),1f).apply{setMargins(dp(4),dp(4),dp(4),dp(4))})
        overlay.addView(row)
    }
    private fun hidePanel(){overlay.visibility=View.GONE;root.isFocusableInTouchMode=true;root.requestFocus()}
    fun showChannels() {
        screen="channels";toolbar("智选直播")
        val search=EditText(this).apply {
            hint="搜索频道名称";setTextColor(Color.WHITE);setHintTextColor(0xFFADB9C7.toInt());textSize=17f;isSingleLine=true;setText(query)
            contentDescription="搜索频道名称";setSelectAllOnFocus(true)
        }
        overlay.addView(search,LinearLayout.LayoutParams(-1,dp(48)))
        val body=LinearLayout(this).apply{orientation=LinearLayout.HORIZONTAL}
        val categories=LinearLayout(this).apply{orientation=LinearLayout.VERTICAL}
        val categoryScroll=ScrollView(this).apply{addView(categories)}
        body.addView(categoryScroll,LinearLayout.LayoutParams(dp(175),-1))
        val channelColumn=LinearLayout(this).apply{orientation=LinearLayout.VERTICAL}
        body.addView(channelColumn,LinearLayout.LayoutParams(0,-1,1f))
        overlay.addView(body,LinearLayout.LayoutParams(-1,0,1f))
        var firstChannel:View?=null
        fun renderList() {
            channelColumn.removeAllViews()
            val all=catalog?.channels?:emptyList()
            val filtered=all.filter { c ->
                (when(category){"全部"->true;"收藏"->store.favorites().contains(c.id);"最近"->store.recent().contains(c.id);else->c.group==category}) && c.name.contains(query,true)
            }.let { list -> if(category=="最近")list.sortedBy{store.recent().indexOf(it.id)} else list }
            page=clampChannelPage(page,filtered.size)
            channelColumn.addView(text("$category · ${filtered.size} 个频道 · OK 播放，返回键关闭菜单",15))
            if(filtered.isEmpty()) {
                channelColumn.addView(text(if(all.isEmpty()) "当前没有已授权的可用频道。\n服务端默认拒绝未经确认授权的来源，因此空目录属于正常保护状态。\n可在设置中填写自己的已授权 HTTPS 目录。\n\n$loadMessage" else "没有匹配频道。试试其他分类、搜索词，或收藏正在观看的频道。",19))
            } else {
                val entries=LinearLayout(this).apply{orientation=LinearLayout.VERTICAL;id=CHANNEL_LIST_ID}
                firstChannel=null
                filtered.drop(page*80).take(80).forEach { channel ->
                    val b=button("${if(store.favorites().contains(channel.id))"★ " else ""}${channel.name}  ·  ${channel.sources.size} 源") {engine.play(channel);hidePanel()}
                    if(firstChannel==null)firstChannel=b
                    entries.addView(b,LinearLayout.LayoutParams(-1,dp(49)).apply{setMargins(dp(4),dp(3),dp(4),dp(3))})
                }
                channelColumn.addView(ScrollView(this).apply{addView(entries)},LinearLayout.LayoutParams(-1,0,1f))
                if(filtered.size>80) {
                    val nav=LinearLayout(this)
                    nav.addView(button("上一页"){page=(page-1).coerceAtLeast(0);renderList()},LinearLayout.LayoutParams(0,dp(44),1f))
                    nav.addView(button("${page+1}/${(filtered.size+79)/80} 下一页"){page=if((page+1)*80<filtered.size)page+1 else 0;renderList()},LinearLayout.LayoutParams(0,dp(44),1f))
                    channelColumn.addView(nav)
                }
            }
        }
        (listOf("全部","收藏","最近")+(catalog?.channels?.map{it.group}?.distinct()?.sorted()?:emptyList())).distinct().forEach { label ->
            categories.addView(button(if(category==label)"● $label" else label){category=label;page=0;showChannels()},LinearLayout.LayoutParams(-1,dp(47)).apply{setMargins(0,dp(3),dp(8),dp(3))})
        }
        search.addTextChangedListener(object:TextWatcher {override fun beforeTextChanged(s:CharSequence?,start:Int,count:Int,after:Int){};override fun onTextChanged(s:CharSequence?,start:Int,before:Int,count:Int){query=s.toString();page=0;renderList()};override fun afterTextChanged(s:Editable?){} })
        renderList()
        engine.channel?.let { selected ->
            overlay.addView(button(if(store.favorites().contains(selected.id))"★ 取消收藏 ${selected.name}" else "☆ 收藏 ${selected.name}"){store.toggleFavorite(selected.id);showChannels()},LinearLayout.LayoutParams(-1,dp(44)))
        }
        (firstChannel?:findViewById<View>(SETTINGS_ID)).requestFocus()
    }
    fun showSettings() {
        screen="settings";toolbar("设置")
        val content=LinearLayout(this).apply{orientation=LinearLayout.VERTICAL}
        val scroll=ScrollView(this).apply{addView(content)}
        overlay.addView(scroll,LinearLayout.LayoutParams(-1,0,1f))
        content.addView(text("频道目录地址（HTTPS）",19))
        val address=EditText(this).apply{setText(store.configUrl);textSize=17f;setTextColor(Color.WHITE);isSingleLine=true;inputType=android.text.InputType.TYPE_CLASS_TEXT or android.text.InputType.TYPE_TEXT_VARIATION_URI;setSelectAllOnFocus(true);contentDescription="HTTPS频道目录地址"}
        content.addView(address,LinearLayout.LayoutParams(-1,dp(54)))
        content.addView(button("保存并更新目录") {
            try {store.configUrl=address.text.toString();catalog=repository.cached(store.configUrl)?.catalog;loadMessage="正在更新新目录…";showChannels();refreshCatalog()}
            catch(e:Exception){Toast.makeText(this,e.message?:"配置地址无效",Toast.LENGTH_LONG).show()}
        },LinearLayout.LayoutParams(-1,dp(48)))
        content.addView(button("备用 HTTP 预检：${if(store.preflight)"开启" else "关闭（低资源默认）"}"){store.preflight=!store.preflight;showSettings()},LinearLayout.LayoutParams(-1,dp(48)))
        content.addView(text("预检只检查下一个 HTTPS 地址的连通性，不预加载视频，不创建备用播放器。不能证明备用源可解码。",15))
        content.addView(button("缓冲策略：${if(store.stableBuffer)"稳定优先" else "均衡"}"){store.stableBuffer=!store.stableBuffer;showSettings()},LinearLayout.LayoutParams(-1,dp(48)))
        content.addView(text("切台后应用新缓冲策略。发生故障时自动提高缓冲上限。实际缓存受带宽、媒体时间轴和内存上限影响。",15))
        content.addView(button("刷新频道目录"){refreshCatalog();Toast.makeText(this,"正在后台更新",Toast.LENGTH_SHORT).show()},LinearLayout.LayoutParams(-1,dp(48)))
        content.addView(button("清除本地观看记录与质量统计"){store.clearHistory();Toast.makeText(this,"已清除",Toast.LENGTH_SHORT).show()},LinearLayout.LayoutParams(-1,dp(48)))
        content.addView(text("版本 ${BuildConfig.VERSION_NAME}\n测试版，尚未上架，暂无商店更新渠道。\n观看数据只保存在这台设备，不上传。\n目录状态：$loadMessage",16))
        address.requestFocus()
    }
    fun showDiagnostics() {
        screen="diagnostics";toolbar("播放诊断")
        val content=LinearLayout(this).apply{orientation=LinearLayout.VERTICAL}
        overlay.addView(ScrollView(this).apply{addView(content)},LinearLayout.LayoutParams(-1,0,1f))
        content.addView(text(engine.diagnostics(),16))
        val refresh=button("刷新诊断"){showDiagnostics()};content.addView(refresh,LinearLayout.LayoutParams(-1,dp(46)))
        content.addView(button("导出脱敏日志到文件") {
            val file=log.export(engine.diagnostics());Toast.makeText(this,"已保存：${file.absolutePath}",Toast.LENGTH_LONG).show()
            content.addView(text("导出完成：${file.absolutePath}\n可通过电视文件管理器或 ADB 取出。",15))
        },LinearLayout.LayoutParams(-1,dp(46)))
        content.addView(text("以下为本应用自制离线测试，不属于直播频道目录。",16))
        content.addView(button("播放离线彩条与测试音"){startDiagnostic("normal")},LinearLayout.LayoutParams(-1,dp(46)))
        content.addView(button("测试：坏媒体 → 彩条备用源"){startDiagnostic("fallback")},LinearLayout.LayoutParams(-1,dp(46)))
        content.addView(button("测试：所有来源失效"){startDiagnostic("all-failed")},LinearLayout.LayoutParams(-1,dp(46)))
        refresh.requestFocus()
    }
    fun startDiagnostic(mode:String="normal") {
        val good=Source("diagnostic-good","asset:///diagnostic.mp4","progressive",90.0,originGroup="self-made")
        val bad=Source("diagnostic-invalid","asset:///invalid.bin","progressive",100.0,originGroup="self-made")
        val missing=Source("diagnostic-missing","asset:///missing.mp4","progressive",99.0,originGroup="self-made")
        val audio=Source("diagnostic-audio-only","asset:///audio-only.m4a","progressive",100.0,originGroup="self-made")
        val sources=when(mode){"fallback"->listOf(bad,good);"audio-only"->listOf(audio,good);"all-failed"->listOf(bad,missing);else->listOf(good)}
        // 诊断源排序固定，不让历史故障改变下一轮测试故障触发顺序。
        sources.forEach{store.updateQuality("${engine.network}:${it.id}"){Quality()}}
        engine.play(Channel("__diagnostic__","本地诊断 · $mode","诊断","",sources));hidePanel()
    }
    private fun refreshCatalog() {
        val url=store.configUrl;val token=++refreshEpoch
        repository.cached(url)?.let{catalog=it.catalog;loadMessage="已加载本地有效缓存；正在检查目录更新";if(screen=="channels" && panelVisible)showChannels()}
        worker.execute {
            val result=repository.refresh(url)
            runOnUiThread {
                if(!isDestroyed && token==refreshEpoch) {
                    catalog=result.catalog;loadMessage=result.message
                    log.event("catalog_refresh","channels=${result.catalog?.channels?.size?:0} cached=${result.cached} status=${result.message}")
                    if(panelVisible && screen=="channels")showChannels()
                }
            }
        }
    }
    override fun onKeyDown(keyCode:Int,event:KeyEvent):Boolean {
        if(keyCode==KeyEvent.KEYCODE_BACK){if(panelVisible)hidePanel() else finish();return true}
        if(!panelVisible) {
            when(keyCode) {
                KeyEvent.KEYCODE_DPAD_CENTER,KeyEvent.KEYCODE_ENTER,KeyEvent.KEYCODE_MENU->{showChannels();return true}
                KeyEvent.KEYCODE_DPAD_UP,KeyEvent.KEYCODE_CHANNEL_UP->{changeChannel(1);return true}
                KeyEvent.KEYCODE_DPAD_DOWN,KeyEvent.KEYCODE_CHANNEL_DOWN->{changeChannel(-1);return true}
                KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE->{showChannels();return true}
            }
        }
        return super.onKeyDown(keyCode,event)
    }
    private fun changeChannel(delta:Int) {
        val channels=catalog?.channels?:emptyList();if(channels.isEmpty()){showChannels();return}
        val current=channels.indexOfFirst{it.id==engine.channel?.id};engine.play(channels[((current+delta)%channels.size+channels.size)%channels.size])
    }
}
