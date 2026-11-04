package com.luolihao.smarttv

private fun menuGroup(group:String)=when(group){"卫视","地方频道"->"地方台";else->group}

fun channelCategories(channels:List<Channel>):List<String> {
    val groups=channels.map{menuGroup(it.group)}.filter{it.isNotBlank()}.distinct()
    val primary=listOf("央视","地方台")
    val remaining=groups.filter{it !in primary}.sortedWith(compareBy({when(it){"香港"->0;"台湾"->1;else->2}},{it}))
    return (primary+remaining).distinct()
}

fun menuChannels(channels:List<Channel>,category:String,query:String,favorites:Set<String>,recent:List<String>):List<Channel> =
    channels.filter { channel ->
        (when(category){"全部"->true;"收藏"->channel.id in favorites;"最近"->channel.id in recent;else->menuGroup(channel.group)==category}) && channel.name.contains(query,true)
    }.let{if(category=="最近")it.sortedBy{channel->recent.indexOf(channel.id)}else it}
