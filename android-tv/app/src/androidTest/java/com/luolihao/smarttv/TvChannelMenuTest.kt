package com.luolihao.smarttv

import android.content.Intent
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONArray
import org.json.JSONObject
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

@RunWith(AndroidJUnit4::class)
class TvChannelMenuTest {
    private val instrumentation=InstrumentationRegistry.getInstrumentation()
    private lateinit var activity:MainActivity
    private lateinit var previousUrl:String
    @Before fun launch() {
        val context=instrumentation.targetContext
        previousUrl=LocalStore(context).configUrl
        val url="https://127.0.0.1/unavailable-menu-test.json"
        val channels=JSONArray()
        (listOf("CCTV-1","CCTV-2")+listOf("湖南卫视","江苏卫视","浙江卫视","北京卫视")).forEachIndexed { index,name ->
            channels.put(JSONObject().put("id","test-$index").put("name",name).put("group",if(index<2)"央视" else "卫视")
                .put("sources",JSONArray().put(JSONObject().put("id","src-$index").put("url","https://example.invalid/$index.m3u8").put("type","hls").put("score",50))))
        }
        val doc=JSONObject().put("schemaVersion",1).put("minClientVersion",1).put("generatedAt","2026-11-04T00:00:00Z")
            .put("channels",channels).put("contentHash",CatalogParser.hashChannels(channels))
        CatalogCache(File(context.filesDir,"catalog-cache")).save(url,doc.toString(),"")
        LocalStore(context).configUrl=url
        activity=instrumentation.startActivitySync(Intent(context,MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as MainActivity
        instrumentation.waitForIdleSync()
        instrumentation.runOnMainSync{assertFalse("启动时菜单应隐藏",activity.panelVisible)}
        key(KeyEvent.KEYCODE_DPAD_CENTER)
    }
    @After fun close() {
        instrumentation.runOnMainSync{activity.finish()}
        LocalStore(instrumentation.targetContext).configUrl=previousUrl
        instrumentation.waitForIdleSync()
    }
    private fun find(description:String,view:View=activity.window.decorView):View? {
        if(view.contentDescription==description)return view
        if(view is ViewGroup)for(index in 0 until view.childCount)find(description,view.getChildAt(index))?.let{return it}
        return null
    }
    private fun key(code:Int){instrumentation.sendKeyDownUpSync(code);instrumentation.waitForIdleSync()}
    @Test fun channelMenuIsALeftSidebarAndRightSideStaysVisible() {
        instrumentation.runOnMainSync {
            val panel=find("频道侧栏")
            assertNotNull("选台应显示在左侧栏",panel)
            assertTrue(panel!!.width<activity.window.decorView.width*0.7)
            assertNotNull(find("分类：央视"));assertNotNull(find("分类：地方台"))
            assertNull(find("分类：台湾"))
        }
    }
    @Test fun remoteMovesBetweenCategoryAndItsChannelsThenPlays() {
        instrumentation.runOnMainSync{find("分类：地方台")!!.requestFocus()}
        key(KeyEvent.KEYCODE_DPAD_RIGHT)
        instrumentation.runOnMainSync{assertEquals("频道：湖南卫视",activity.currentFocus?.contentDescription)}
        key(KeyEvent.KEYCODE_DPAD_DOWN)
        instrumentation.runOnMainSync{assertEquals("频道：江苏卫视",activity.currentFocus?.contentDescription)}
        key(KeyEvent.KEYCODE_DPAD_LEFT)
        instrumentation.runOnMainSync{assertEquals("分类：地方台",activity.currentFocus?.contentDescription)}
        key(KeyEvent.KEYCODE_DPAD_RIGHT);key(KeyEvent.KEYCODE_DPAD_CENTER)
        instrumentation.runOnMainSync{assertEquals("湖南卫视",activity.engine.channel?.name);assertTrue(activity.panelVisible);assertEquals(1,activity.engine.activePlayerCount)}
        key(KeyEvent.KEYCODE_DPAD_DOWN)
        instrumentation.runOnMainSync{assertEquals("湖南卫视",activity.engine.channel?.name)}
        key(KeyEvent.KEYCODE_BACK)
        instrumentation.runOnMainSync{assertFalse(activity.panelVisible)}
        key(KeyEvent.KEYCODE_DPAD_CENTER)
        instrumentation.runOnMainSync{assertTrue(activity.panelVisible);assertEquals("频道：湖南卫视",activity.currentFocus?.contentDescription)}
        key(KeyEvent.KEYCODE_BACK)
        instrumentation.runOnMainSync{assertFalse(activity.panelVisible)}
    }
    @Test fun viewerOnlySeesCategoriesAndChannelsWithoutUtilityButtons() {
        instrumentation.runOnMainSync {
            for(label in listOf("设置","搜索","诊断","分类：收藏","分类：最近","分类：全部"))assertNull(label,find(label))
        }
    }
    @Test fun defaultStartupLoadsBundledRealCatalogWithoutConfiguration() {
        instrumentation.runOnMainSync{activity.finish()}
        LocalStore(instrumentation.targetContext).configUrl=LocalStore.TV_TRIAL_URL
        activity=instrumentation.startActivitySync(Intent(instrumentation.targetContext,MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)) as MainActivity
        instrumentation.waitForIdleSync()
        instrumentation.runOnMainSync{assertFalse(activity.panelVisible);assertNotNull(activity.engine.channel)}
        key(KeyEvent.KEYCODE_DPAD_CENTER)
        instrumentation.runOnMainSync {
            assertNotNull(find("频道：CCTV-1 综合"))
            val saved=CatalogCache(File(activity.filesDir,"catalog-cache")).read(LocalStore.TV_TRIAL_URL)
            assertEquals(14,saved?.catalog?.channels?.size)
            assertEquals(setOf("央视","地方台"),saved!!.catalog.channels.map{it.group}.toSet())
        }
    }
}
