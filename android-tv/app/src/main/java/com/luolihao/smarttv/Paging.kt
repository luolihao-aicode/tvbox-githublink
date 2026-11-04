package com.luolihao.smarttv

fun clampChannelPage(page:Int,total:Int,pageSize:Int=80):Int {
    require(pageSize>0)
    return page.coerceIn(0,((total.coerceAtLeast(1)-1)/pageSize))
}
