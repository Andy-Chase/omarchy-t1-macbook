/* Physical Touch Bar forecast view. Icons mirror Omarchy weather/Model.js. */
static const char *weather_glyph(int code){
    if(code==0)return "";
    if(code==1||code==2)return "";
    if(code==3)return "";
    if(code==45||code==48)return "";
    if((code>=51&&code<=57)||code==61)return "";
    if((code>=63&&code<=67)||(code>=80&&code<=82))return "";
    if((code>=71&&code<=77)||code==85||code==86)return "";
    if(code==95||code==96||code==99)return "";
    return "?";
}
static bool parse_weather(const char *packet){
    long values[32];int n=0;const char *p=packet+8;
    while(*p){
        if(n>=32)return false;
        char *end;errno=0;long value=strtol(p,&end,10);
        if(end==p||errno||value< -100000||value>100000||(*end&&*end!=' '))return false;
        values[n++]=value;p=end;if(*p)p++;
    }
    if(n<4||values[0]< -1||values[0]>2||values[2]<0||values[2]>7||values[3]<0||values[3]>1||n!=4+values[2]*4)return false;
    if(values[1]!=-1&&(values[1]<0xe300||values[1]>0xe3ff))return false;
    WeatherReport next={.status=values[0],.icon=values[1],.count=values[2],.fahrenheit=values[3]};
    for(int i=0;i<next.count;i++){
        int index=4+i*4;int date=values[index];int absolute=abs(date);
        if(absolute<19000||absolute>47481||(date<0&&i!=0)||values[index+1]<0||values[index+1]>99||values[index+2]< -150||values[index+2]>160||values[index+3]< -150||values[index+3]>values[index+2])return false;
        if(i&&absolute!=abs(next.days[i-1][0])+1)return false;
        for(int j=0;j<4;j++)next.days[i][j]=(int)values[index+j];
    }
    weather_report=next;memset(hourly_reports,0,sizeof hourly_reports);return true;
}
static bool weather_code_valid(long code){
    const int codes[]={0,1,2,3,45,48,51,53,55,56,57,61,63,65,66,67,71,73,75,77,80,81,82,85,86,95,96,99};
    for(size_t i=0;i<sizeof codes/sizeof codes[0];i++)if(code==codes[i])return true;
    return false;
}
static bool parse_hourly(const char *packet){
    long v[125];int n=0;const char *p=packet+6;
    while(*p){
        if(n>=125)return false;
        char *end;errno=0;long value=strtol(p,&end,10);
        if(end==p||errno||value< -100000||value>100000||(*end&&*end!=' '))return false;
        v[n++]=value;p=end;if(*p)p++;
    }
    if(n<5||v[0]<1||v[0]>2||v[1]<19000||v[1]>47481||v[2]<0||v[2]>24||v[3]<0||v[3]>1||v[4]< -1||v[4]>23||n!=5+v[2]*5)return false;
    int slot=-1;for(int i=0;i<weather_report.count;i++)if(abs(weather_report.days[i][0])==v[1])slot=i;
    if(slot<0||v[3]!=weather_report.fahrenheit||v[0]!=weather_report.status)return false;
    HourlyReport next={.status=v[0],.date=v[1],.count=v[2],.fahrenheit=v[3],.current_hour=v[4]};
    for(int i=0;i<next.count;i++){
        int j=5+i*5;
        if(v[j]<0||v[j]>23||(i&&v[j]<=next.hours[i-1][0])||!weather_code_valid(v[j+1])||v[j+2]< -150||v[j+2]>160||v[j+3]< -1||v[j+3]>100||v[j+4]< -1||v[j+4]>1)return false;
        for(int k=0;k<5;k++)next.hours[i][k]=(int)v[j+k];
    }
    hourly_reports[slot]=next;return true;
}
static HourlyReport *selected_hours(void){
    for(int i=0;i<7;i++)if(hourly_reports[i].date==weather_date)return &hourly_reports[i];
    return NULL;
}
static int weather_hit(double x){
    if(weather_date){
        if(x>=360&&x<460&&weather_hour_page>0)return HOURS_PREV;
        if(x>=control_x(THEME)-100&&x<control_x(THEME)&&weather_hour_page<3)return HOURS_NEXT;
        return 0;
    }
    if(x>=260&&x<control_x(THEME)){int index=(int)((x-260)/((control_x(THEME)-260)/7));if(index<weather_report.count)return WEATHER_DAY+index;}
    return 0;
}
static bool weather_navigate(int action){
    if(action==WEATHER){weather_mode=true;weather_date=0;weather_hour_page=0;return true;}
    if(!weather_mode)return false;
    if(action==WEATHER_BACK){if(weather_date)weather_date=0;else weather_mode=false;return true;}
    if(action>=WEATHER_DAY&&action<WEATHER_DAY+weather_report.count){
        weather_date=abs(weather_report.days[action-WEATHER_DAY][0]);
        HourlyReport *report=selected_hours();weather_hour_page=report&&report->current_hour>=0?report->current_hour/6:0;return true;
    }
    if(action==HOURS_PREV&&weather_date&&weather_hour_page>0){weather_hour_page--;return true;}
    if(action==HOURS_NEXT&&weather_date&&weather_hour_page<3){weather_hour_page++;return true;}
    return false;
}
static void weather_icon(cairo_t *cr,const char *glyph,double x,double width,double y,double size){
    cairo_save(cr);cairo_select_font_face(cr,"JetBrainsMono Nerd Font",CAIRO_FONT_SLANT_NORMAL,CAIRO_FONT_WEIGHT_NORMAL);
    cairo_translate(cr,0,y-30);text(cr,glyph,x,width,size);cairo_restore(cr);
}
static void weather_text(cairo_t *cr,const char *label,double x,double width,double y,double size){
    cairo_save(cr);cairo_translate(cr,0,y-30);text(cr,label,x,width,size);cairo_restore(cr);
}
static void weather_button(cairo_t *cr,int pressed){
    double x=7*key_width;
    button_colour(cr,pressed==WEATHER);
    if(weather_report.icon>0){
        unsigned c=(unsigned)weather_report.icon;
        char glyph[4]={(char)(0xe0|(c>>12)),(char)(0x80|((c>>6)&63)),(char)(0x80|(c&63)),0};
        weather_icon(cr,glyph,x,key_width,30,33);
    }else text(cr,weather_report.status==0?"…":"?",x,key_width,23);
    if(weather_report.status==2){cairo_arc(cr,x+key_width-20,49,2,0,2*M_PI);cairo_fill(cr);}
}
static void hourly_paint(cairo_t *cr,int pressed){
    button_colour(cr,pressed==WEATHER_BACK);text(cr,"Back",0,key_width,27);button_colour(cr,false);
    char name[32];time_t date=(time_t)weather_date*86400;struct tm calendar;gmtime_r(&date,&calendar);strftime(name,sizeof name,"%a %d %b",&calendar);
    weather_text(cr,name,100,260,17,23);
    HourlyReport *report=selected_hours();
    weather_text(cr,weather_report.status==2?(weather_report.fahrenheit?"Cached °F · rain %":"Cached °C · rain %"):(weather_report.fahrenheit?"°F · rain %":"°C · rain %"),100,260,44,19);
    if(weather_hour_page>0){button_colour(cr,pressed==HOURS_PREV);text(cr,"‹",360,100,38);}
    if(weather_hour_page<3){button_colour(cr,pressed==HOURS_NEXT);text(cr,"›",control_x(THEME)-100,100,38);}
    button_colour(cr,false);
    if(!report||!report->count){text(cr,"Hourly forecast unavailable for this day",460,control_x(THEME)-560,25);theme_button(cr,pressed);return;}
    for(int i=0;i<6;i++){
        int hour=weather_hour_page*6+i;int *row=NULL;
        for(int j=0;j<report->count;j++)if(report->hours[j][0]==hour)row=report->hours[j];
        double width=(control_x(THEME)-560)/6,x=460+i*width;
        button_colour(cr,hour==report->current_hour);
        char label[32],values[48];snprintf(label,sizeof label,"%02d:00",hour);
        weather_text(cr,label,x+52,width-60,15,21);
        if(row){
            const char *glyph=weather_glyph(row[1]);
            if(row[4]==0){if(row[1]==0)glyph="";else if(row[1]==1||row[1]==2)glyph="";}
            weather_icon(cr,glyph,x+5,50,31,35);
            if(row[3]<0)snprintf(values,sizeof values,"%d°  —",row[2]);else snprintf(values,sizeof values,"%d°  %d%%",row[2],row[3]);
        }else strcpy(values,"—");
        weather_text(cr,values,x+52,width-60,43,23);
        cairo_set_source_rgb(cr,.18,.22,.27);cairo_rectangle(cr,x+width-1,10,1,40);cairo_fill(cr);
    }
    theme_button(cr,pressed);
}
static void weather_paint(cairo_t *cr,int pressed){
    if(weather_date){hourly_paint(cr,pressed);return;}
    button_colour(cr,pressed==WEATHER_BACK);text(cr,"Back",0,key_width,27);button_colour(cr,false);
    weather_text(cr,weather_report.status==2?"Cached":"7 days",100,160,18,21);
    weather_text(cr,weather_report.fahrenheit?"°F":"°C",100,160,43,22);
    if(!weather_report.count){
        const char *message=weather_report.status==0?"Loading the top bar’s forecast…":"Weather unavailable — waiting for the top bar";
        text(cr,message,260,control_x(THEME)-260,28);
    }else for(int i=0;i<7;i++){
        double width=(control_x(THEME)-260)/7,x=260+i*width;
        if(i>=weather_report.count){cairo_set_source_rgb(cr,.4,.45,.5);text(cr,"—",x,width,24);continue;}
        int *day=weather_report.days[i];
        button_colour(cr,day[0]<0||pressed==WEATHER_DAY+i);weather_icon(cr,weather_glyph(day[1]),x+8,52,30,41);
        char name[32],temp[32];time_t date=(time_t)abs(day[0])*86400;struct tm calendar;
        gmtime_r(&date,&calendar);
        if(day[0]<0)strcpy(name,"Today");else strftime(name,sizeof name,"%a %d",&calendar);
        weather_text(cr,name,x+65,width-75,16,21);
        snprintf(temp,sizeof temp,"%d° / %d°",day[2],day[3]);
        weather_text(cr,temp,x+65,width-75,43,24);
        cairo_set_source_rgb(cr,.18,.22,.27);cairo_rectangle(cr,x+width-1,10,1,40);cairo_fill(cr);
    }
    theme_button(cr,pressed);
}
