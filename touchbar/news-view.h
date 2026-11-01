/* News display: only fixed action identifiers and article tokens reach the bridge. */
static bool news_command(int bridge,const char *request){
    size_t n=strlen(request);return send(bridge,request,n,MSG_NOSIGNAL)==(ssize_t)n;
}
static bool news_utf8(const unsigned char *s){
    while(*s){
        unsigned c=*s++;if(c<32||c==127)return false;if(c<128)continue;
        int n;unsigned value,min;
        if(c>=0xc2&&c<=0xdf){n=1;value=c&31;min=128;}
        else if(c>=0xe0&&c<=0xef){n=2;value=c&15;min=2048;}
        else if(c>=0xf0&&c<=0xf4){n=3;value=c&7;min=65536;}else return false;
        for(int i=0;i<n;i++){if((*s&0xc0)!=0x80)return false;value=(value<<6)|(*s++&63);}
        if(value<min||value>0x10ffff||(value>=0xd800&&value<=0xdfff))return false;
    }return true;
}
static bool parse_news(const char *message){
    NewsReport next={0};int offset=0;
    if(sscanf(message,"NEWS %d %d %16s %lld %d %d%n",&next.category,&next.status,next.token,&next.published,&next.position,&next.count,&offset)!=6)return false;
    if(next.category<0||next.category>1||next.status< -1||next.status>2||next.count<0||next.count>20||next.position<0||next.position>next.count||next.published<0||next.published>4102444800LL||message[offset]!='\t')return false;
    bool article=next.count>0;
    if(article){if(strlen(next.token)!=16||next.position<1||next.status<1)return false;for(int i=0;i<16;i++)if(!((next.token[i]>='0'&&next.token[i]<='9')||(next.token[i]>='a'&&next.token[i]<='f')))return false;}
    else if(strcmp(next.token,"-")||next.position||next.status>0)return false;
    const char *title=message+offset+1;size_t length=strlen(title);
    if(!length||length>=sizeof next.title||!news_utf8((const unsigned char *)title))return false;
    strcpy(next.title,title);
    news_last_category=next.category;
    if(news_frozen){news_pending=next;news_pending_valid=true;return true;}
    if(strcmp(news_report.token,next.token)||news_report.category!=next.category)news_started=now();
    news_report=next;news_next_wait=false;return true;
}
static int news_hit(double x,State state){
    if(state.locked)return 0;
    if(x<key_width)return NEWS_EXIT;
    if(x>=control_x(BATTERY))return BATTERY;
    if(news_menu){if(x>=200&&x<360)return NEWS_AI;if(x>=400&&x<600)return NEWS_GLOBAL;return 0;}
    return NEWS_HEADLINE;
}
/* Only a single-finger headline contact can become a swipe. Track the full
 * excursion so a drag out and back cannot accidentally become a tap/hold. */
static double news_touch_start,news_touch_last,news_touch_excursion;
static void news_touch_begin(double x){news_touch_start=x;news_touch_last=x;news_touch_excursion=0;}
static void news_touch_move(double x){news_touch_last=x;news_touch_excursion=fmax(news_touch_excursion,fabs(x-news_touch_start));}
static bool news_touch_moved(void){return news_touch_excursion>18;}
static int news_swipe(bool cancelled,bool held){
    if(cancelled||held||!news_report.count)return 0;
    double delta=news_touch_last-news_touch_start;
    return delta<=-120?1:delta>=120?-1:0;
}
static bool news_hold_due(int action,bool contact,bool cancelled,bool handled,double elapsed){
    return action==NEWS_HEADLINE&&contact&&!cancelled&&!handled&&!news_touch_moved()&&elapsed>=.65;
}
static void news_icon(cairo_t *cr,double x){
    double left=round(x+key_width/2)-14;cairo_set_line_width(cr,2);
    cairo_rectangle(cr,left,18,28,25);cairo_rectangle(cr,left+4,23,8,7);
    cairo_move_to(cr,left+16,24);cairo_line_to(cr,left+24,24);
    cairo_move_to(cr,left+16,29);cairo_line_to(cr,left+24,29);
    cairo_move_to(cr,left+4,35);cairo_line_to(cr,left+24,35);
    cairo_move_to(cr,left+4,39);cairo_line_to(cr,left+24,39);cairo_stroke(cr);
}
static double news_overflow;
static double news_elapsed(void){return fmax(0,(news_frozen?news_frozen_at:now())-news_started);}
static double news_dwell(void){return fmax(10,4+news_overflow/50);}
static void news_category_label(cairo_t *cr,const char *label,double x){
    cairo_text_extents_t ext;cairo_set_font_size(cr,workspace_font_size);cairo_text_extents(cr,label,&ext);
    cairo_move_to(cr,round(x-ext.x_bearing),round(30-ext.height/2-ext.y_bearing));cairo_show_text(cr,label);
}
static void news_panel(cairo_t *cr,int pressed){
    button_colour(cr,pressed==NEWS_EXIT);text(cr,"esc",0,key_width,27);battery_label(cr);
    if(news_menu){
        button_colour(cr,false);news_icon(cr,100);
        button_colour(cr,pressed==NEWS_AI||news_last_category==0);news_category_label(cr,"AI",220);
        button_colour(cr,pressed==NEWS_GLOBAL||news_last_category==1);news_category_label(cr,"Global",420);
        return;
    }
    /* Source/age is kept still while only an overlong title moves. */
    double x=100,width=1450;cairo_text_extents_t ext;cairo_set_font_size(cr,workspace_font_size);
    cairo_text_extents(cr,news_report.title[0]?news_report.title:"Loading headlines…",&ext);
    news_overflow=fmax(0,ext.width-width+8);
    double offset=fmin(news_overflow,fmax(0,news_elapsed()-2)*50);
    cairo_save(cr);cairo_rectangle(cr,x,0,width,60);cairo_clip(cr);
    button_colour(cr,pressed==NEWS_HEADLINE);
    cairo_move_to(cr,round(x-offset),round(30-ext.height/2-ext.y_bearing));
    cairo_show_text(cr,news_report.title[0]?news_report.title:"Loading headlines…");cairo_restore(cr);
    char age[48]="",meta[160];
    if(news_report.published){long long seconds=(long long)time(NULL)-news_report.published;if(seconds<0)seconds=0;
        if(seconds<60)snprintf(age,sizeof age," · now");
        else if(seconds<3600)snprintf(age,sizeof age," · %lldm",seconds/60);
        else if(seconds<86400)snprintf(age,sizeof age," · %lldh",seconds/3600);
        else snprintf(age,sizeof age," · %lldd",seconds/86400);
    }
    const char *source=news_report.category==1?"BBC World":"The Verge";
    if(news_report.count)snprintf(meta,sizeof meta,"%s%s · %d/%d%s",source,age,news_report.position,news_report.count,news_report.status==2?" · cached":"");
    else snprintf(meta,sizeof meta,"%s · %s",source,news_report.status<0?"offline":"loading");
    button_colour(cr,false);text(cr,meta,1570,500,18);
}
static bool news_action(int bridge,int action,bool held){
    if(action==NEWS){news_mode=true;news_menu=true;news_started=now();return true;}
    if(action==NEWS_EXIT){news_mode=false;news_menu=false;return news_command(bridge,"NEWS CLOSE");}
    if(action==NEWS_AI||action==NEWS_GLOBAL){
        int category=action==NEWS_GLOBAL;news_mode=true;news_menu=false;news_started=now();news_next_wait=false;news_pending_valid=false;
        if(news_report.category!=category)news_report=(NewsReport){.category=category};
        return news_command(bridge,category?"NEWS SELECT 1":"NEWS SELECT 0");
    }
    if(action==NEWS_HEADLINE&&!held&&news_report.count){
        char request[64];snprintf(request,sizeof request,"NEWS ARTICLE %s",news_report.token);
        return news_command(bridge,request);
    }return true;
}
