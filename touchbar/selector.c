// SPDX-License-Identifier: GPL-2.0-only
#define _GNU_SOURCE
#include <assert.h>
#include <cairo/cairo.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <limits.h>
#include <linux/hidraw.h>
#include <linux/input.h>
#include <linux/uinput.h>
#include <math.h>
#include <poll.h>
#include <pwd.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>
#include <xf86drm.h>
#include <xf86drmMode.h>
#include <drm_fourcc.h>

enum { ESCAPE=100, TOGGLE=101, MUTE=104, BACK=107, OPEN_B=110, OPEN_V, OPEN_K, SLIDE=120, MINUS, PLUS, DONE, THEME=130, CHATGPT=131, WEATHER=132, WEATHER_BACK=133, MIC_MUTE=134, PLAYPAUSE=135, HOURS_PREV=136, HOURS_NEXT=137, NIGHTLIGHT=139, FKEY=200, WEATHER_DAY=300 };
static const int fkeys[]={KEY_F1,KEY_F2,KEY_F3,KEY_F4,KEY_F5,KEY_F6,KEY_F7,KEY_F8,KEY_F9,KEY_F10,KEY_F11,KEY_F12};
typedef struct {int active; unsigned occupied,present; bool locked;} State;
static volatile sig_atomic_t stopped;
static const int piano_white[]={0,2,4,5,7,9,11,12,14,16,17,19,21,23,24};
static bool piano_mode,piano_back_contact;
static unsigned piano_mask;
static int piano_status,piano_demo_index,piano_touch_action,piano_demo_page,piano_octave;
static bool piano_sustain;
static bool piano_demo_playing,piano_demo_menu,piano_contact,piano_hold_handled;
static unsigned piano_demo_mask;
static double piano_touch_since;
static const char *piano_demo_names[]={"Für Elise","Ode to Joy","Twinkle, Twinkle","Greensleeves","The Entertainer","Turkish March","Pachelbel Canon","Bach Prelude in C"};
static double piano_last_send;
static bool calibration_mode;
static bool persistent_mode;
static double calibration_x=-1;
static int slider_mode;
static bool weather_mode;
typedef struct {int status,icon,count,fahrenheit;int days[7][4];} WeatherReport;
static WeatherReport weather_report={.icon=-1};
typedef struct {int status,date,count,fahrenheit,current_hour;int hours[24][5];} HourlyReport;
static HourlyReport hourly_reports[7];
static int weather_date,weather_hour_page;
static int weather_hit(double x);
static int levels[3]={-1,-1,-1};
static int muted;
static int battery_percent=-1;
static bool battery_charging;
static double pomodoro_deadline;
static bool battery_mode,wifi_mode,markets_mode;
static bool files_menu;
enum { NEWS=163, NEWS_EXIT=164, NEWS_AI=165, NEWS_GLOBAL=166, NEWS_HEADLINE=167 };
typedef struct {int category,status,position,count;long long published;char token[17],title[1024];} NewsReport;
static bool news_mode,news_menu,news_frozen,news_pending_valid,news_next_wait;
static int news_last_category;
static NewsReport news_report,news_pending;
static double news_started,news_frozen_at,news_next_sent;
static int news_hit(double x,State state);
static const char *files_labels[]={"Home","Documents","Downloads","Desktop","Pictures","Music","Videos"};
static const char *files_targets[]={"DEFAULT","DOCUMENTS","DOWNLOAD","DESKTOP","PICTURES","MUSIC","VIDEOS"};
typedef struct {int direction;char value[48],change[32],stamp[48];bool received;} MarketQuote;
static MarketQuote market_quotes[8];
static char wifi_details[9][128];
static char battery_details[8][48];
enum { BATTERY=138, BATTERY_BACK=140, POWER_PROFILE=141, WIFI=142, WIFI_BACK=143, MARKETS=144, MARKETS_BACK=145, POMODORO=146, TIMER_BACK=147, TIMER_PAUSE=148, TIMER_CLEAR=149, PIANO=150, PIANO_BACK=151, PIANO_DEMO=152, PIANO_PAGE=153, PIANO_DOWN=158, PIANO_UP=159, PIANO_SUSTAIN=160, FILES=161, FILES_BACK=162, FILES_FOLDER=600, PIANO_SONG=500, TIMER_PRESET=400 };
static bool speaker_hold_due(int action,bool contact,bool cancelled,bool handled,double elapsed){
    return action==OPEN_V&&contact&&!cancelled&&!handled&&elapsed>=.6;
}
static bool files_navigate(int action){
    if(action==FILES){files_menu=true;return true;}
    if(action==FILES_BACK){files_menu=false;return true;}
    return false;
}
static bool files_open(int bridge,int index){
    if(index<0||index>=7)return false;
    char request[48];int n=snprintf(request,sizeof request,"FILES OPEN %s",files_targets[index]);
    return send(bridge,request,n,MSG_NOSIGNAL)==n;
}
static bool speaker_mute_request(int bridge){
    const char *request="BUTTON SPEAKER_MUTE";
    return send(bridge,request,strlen(request),MSG_NOSIGNAL)==(ssize_t)strlen(request);
}
/* Internal system batteries only: never report a mouse/headset battery. */
static int read_battery(const char *root){
    battery_charging=false;
    DIR *directory=opendir(root);if(!directory)return -1;
    struct dirent *entry;int result=-1;
    while((entry=readdir(directory))){
        if(entry->d_name[0]=='.')continue;
        char path[PATH_MAX],value[32];
        if(snprintf(path,sizeof path,"%s/%s/type",root,entry->d_name)>=(int)sizeof path)continue;
        FILE *file=fopen(path,"r");if(!file)continue;
        bool is_battery=fgets(value,sizeof value,file)&&!strcmp(value,"Battery\n");fclose(file);if(!is_battery)continue;
        if(snprintf(path,sizeof path,"%s/%s/scope",root,entry->d_name)>=(int)sizeof path)continue;
        file=fopen(path,"r");if(file){bool device=fgets(value,sizeof value,file)&&!strcmp(value,"Device\n");fclose(file);if(device)continue;}
        if(snprintf(path,sizeof path,"%s/%s/capacity",root,entry->d_name)>=(int)sizeof path)continue;
        file=fopen(path,"r");if(!file)continue;
        int percent;char extra;bool valid=fgets(value,sizeof value,file)&&sscanf(value,"%d %c",&percent,&extra)==1&&percent>=0&&percent<=100;fclose(file);
        if(valid){
            result=percent;
            if(snprintf(path,sizeof path,"%s/%s/status",root,entry->d_name)<(int)sizeof path){
                file=fopen(path,"r");if(file){battery_charging=fgets(value,sizeof value,file)&&!strcmp(value,"Charging\n");fclose(file);}
            }
            break;
        }
    }
    closedir(directory);return result;
}
/* -1 means unavailable; never present an unknown microphone as unmuted. */
static int mic_muted=-1,playing=-1,nightlight_enabled=-1;
static const char media_kind[]="BVK";
static const double key_width=80;
static const double workspace_font_size=26;
static bool light_mode;
static char theme_path[PATH_MAX];
static cairo_surface_t *chatgpt_icon;
static double app_feedback_until;
typedef struct {int action;const char *label,*alternate;double width;} Control;
static Control controls[]={{TOGGLE,"Fn","WS",80},{POMODORO,"","",80},{NIGHTLIGHT,"","",80},{PLAYPAUSE,"","",80},{OPEN_B,"","",80},{OPEN_K,"","",80},{MIC_MUTE,"","",80},{OPEN_V,"","",80},{THEME,"","",80},{WIFI,"","",80},{BATTERY,"","",80}};
static double controls_start(void){double width=0;for(size_t i=0;i<sizeof controls/sizeof controls[0];i++)width+=controls[i].width;return 2170-width;}
static double control_x(int action){double x=controls_start();for(size_t i=0;i<sizeof controls/sizeof controls[0];i++){if(controls[i].action==action)return x;x+=controls[i].width;}return -1;}
static const int optional_actions[]={CHATGPT,WEATHER,MARKETS,PIANO,FILES,NEWS};
static const char *optional_names[]={"chatgpt","weather","markets","piano","files","news"};
static bool optional_on[]={true,true,true,true,true,true};
static double optional_end(void){int n=0;for(int i=0;i<6;i++)n+=optional_on[i];return 480+80*n;}
static void load_features(void){
    const char *raw=getenv("OMARCHY_T1_FEATURES");if(!raw)return;
    for(int i=0;i<6;i++)optional_on[i]=false;
    char *copy=strdup(raw);if(!copy)exit(1);
    char *save=NULL;for(char *part=strtok_r(copy,",",&save);part;part=strtok_r(NULL,",",&save)){
        bool found=false;for(int i=0;i<6;i++)if(!strcmp(part,optional_names[i])){optional_on[i]=true;found=true;}
        if(!found){fprintf(stderr,"Unknown optional feature\n");free(copy);exit(2);}
    }free(copy);
}
static double workspace_step(void){return fmin(80,(controls_start()-optional_end())/5);}
static int workspace_slot(int index){return index+(index>=5?5:0);}
static bool battery_navigate(int action){
    if(action==BATTERY){if(wifi_mode){wifi_mode=false;battery_mode=true;}else battery_mode=!battery_mode;return true;}
    if(action==BATTERY_BACK&&battery_mode){battery_mode=false;return true;}
    return false;
}
static void load_app_icon(void){
    /* The original logo mask has no tile; tint it like the other controls. */
    char path[PATH_MAX];const char *home=getenv("HOME");if(!home)return;
    if(snprintf(path,sizeof path,"%s/.local/share/omarchy-touchbar/icons/openai-mask.png",home)>=(int)sizeof path)return;
    chatgpt_icon=cairo_image_surface_create_from_png(path);
    if(cairo_surface_status(chatgpt_icon)!=CAIRO_STATUS_SUCCESS){cairo_surface_destroy(chatgpt_icon);chatgpt_icon=NULL;}
}
static void configure_widths(void){
    cairo_surface_t *s=cairo_image_surface_create(CAIRO_FORMAT_RGB24,1,1);cairo_t *cr=cairo_create(s);
    cairo_select_font_face(cr,"sans",CAIRO_FONT_SLANT_NORMAL,CAIRO_FONT_WEIGHT_NORMAL);cairo_set_font_size(cr,22);
    for(size_t i=0;i<sizeof controls/sizeof controls[0];i++){cairo_text_extents_t a,b;cairo_text_extents(cr,controls[i].label,&a);cairo_text_extents(cr,controls[i].alternate,&b);controls[i].width=controls[i].action==POMODORO?(pomodoro_deadline>0?160:key_width):fmax(key_width,ceil(fmax(a.width,b.width)+24));}
    cairo_destroy(cr);cairo_surface_destroy(s);
}
static void load_theme(const char *home){
    if(snprintf(theme_path,sizeof theme_path,"%s/.config/omarchy/touchbar/theme",home)>=(int)sizeof theme_path){theme_path[0]=0;return;}
    FILE *f=fopen(theme_path,"r");if(!f)return;
    char value[16]={0};if(fgets(value,sizeof value,f))light_mode=!strcmp(value,"light\n")||!strcmp(value,"light");fclose(f);
}
static bool save_theme(void){
    if(!theme_path[0])return false;
    char directory[PATH_MAX],temp[PATH_MAX];strcpy(directory,theme_path);char *slash=strrchr(directory,'/');if(!slash)return false;*slash=0;
    if(mkdir(directory,0700)&&errno!=EEXIST)return false;
    if(snprintf(temp,sizeof temp,"%s.XXXXXX",theme_path)>=(int)sizeof temp)return false;
    int fd=mkstemp(temp);if(fd<0)return false;const char *value=light_mode?"light\n":"dark\n";size_t size=strlen(value);
    bool good=write(fd,value,size)==(ssize_t)size;if(good)good=fsync(fd)==0;if(close(fd))good=false;
    if(good&&rename(temp,theme_path)==0)return true;
    unlink(temp);return false;
}
static void stop(int sig){(void)sig;stopped=1;}
static double now(void){struct timespec t;clock_gettime(CLOCK_MONOTONIC,&t);return t.tv_sec+t.tv_nsec/1e9;}
/* BOOTTIME includes suspend; redraws never determine elapsed time. */
static bool timer_menu,timer_paused;
static double timer_frozen_delta;
static const int timer_presets[]={5,10,15,25};
static double timer_now(void){struct timespec t;clock_gettime(CLOCK_BOOTTIME,&t);return t.tv_sec+t.tv_nsec/1e9;}
/* Signed seconds: zero and negative values are elapsed time after expiry. */
static int pomodoro_remaining(double time){return (int)ceil(timer_paused?timer_frozen_delta:pomodoro_deadline-time);}
static bool pomodoro_warning(int seconds){return seconds>0&&seconds<300;}
static bool timer_select(int action,double time){
    if(action<TIMER_PRESET||action>=TIMER_PRESET+4)return false;
    pomodoro_deadline=time+timer_presets[action-TIMER_PRESET]*60;
    timer_paused=false;timer_frozen_delta=0;
    configure_widths();timer_menu=false;return true;
}
static bool timer_control(int action,double time){
    if(pomodoro_deadline<=0)return false;
    if(action==TIMER_CLEAR){pomodoro_deadline=0;timer_paused=false;timer_frozen_delta=0;configure_widths();return true;}
    if(action!=TIMER_PAUSE)return false;
    if(timer_paused){pomodoro_deadline=time+timer_frozen_delta;timer_paused=false;}
    else {timer_frozen_delta=pomodoro_deadline-time;timer_paused=true;}
    return true;
}
static int ids_for(State s,int ids[10]){int n=0;for(int id=1;id<=10;id++)if(id<=5 || s.active==id || (s.present&(1u<<id)))ids[n++]=id;return n;}
static int hit(double x,bool fn,State s){
    if(x<0 || x>=2170)return 0;
    if(piano_mode)return s.locked?0:(x<80?PIANO_BACK:0);
    if(news_mode&&!battery_mode)return news_hit(x,s);
    if(files_menu){
        if(s.locked)return 0;
        if(x<key_width)return FILES_BACK;
        if(x>=240&&x<2060)return FILES_FOLDER+(int)((x-240)/260);
        return 0;
    }
    if(timer_menu){
        if(s.locked)return 0;
        if(x<key_width)return TIMER_BACK;
        if(pomodoro_deadline>0&&x>=1240&&x<1400)return TIMER_PAUSE;
        if(pomodoro_deadline>0&&x>=1440&&x<1600)return TIMER_CLEAR;
        if(x>=240&&x<880)return TIMER_PRESET+(int)((x-240)/160);
        return 0;
    }
    if(x<key_width&&markets_mode)return MARKETS_BACK;
    if(x<key_width)return wifi_mode?WIFI_BACK:(battery_mode?BATTERY_BACK:(weather_mode?WEATHER_BACK:ESCAPE));
    double theme_x=control_x(THEME);
    if(x>=theme_x&&x<theme_x+key_width)return THEME;
    if(x>=control_x(BATTERY))return BATTERY;
    if(x>=control_x(WIFI)&&x<control_x(WIFI)+key_width)return s.locked?0:WIFI;
    if(markets_mode)return 0;
    if(wifi_mode)return 0;
    if(battery_mode)return !s.locked&&x>=control_x(THEME)-275&&x<control_x(THEME)?POWER_PROFILE:0;
    if(weather_mode)return weather_hit(x);
    if(slider_mode){
        if(x<390)return 0;
        if(x<490)return MINUS;
        if(x<1580)return SLIDE;
        if(x<1680)return PLUS;
        if(x<1780)return slider_mode==2?MUTE:DONE;
        if(slider_mode==2&&x<1880)return DONE;
        return 0;
    }
    if(s.locked&&x<control_x(PLAYPAUSE))return 0;
    if(!fn){
        double edge=optional_end();
        if(x>=edge&&x<fmin(edge+400,controls_start())){int ids[10];int n=ids_for(s,ids);int index=5+(int)((x-edge)/workspace_step());return index<n?ids[index]:0;}
        double left=480;for(int i=0;i<6;i++)if(optional_on[i]){if(x>=left&&x<left+80)return s.locked?0:optional_actions[i];left+=80;}
    }
    if(x<(fn?13:6)*key_width){int ids[10];int n=ids_for(s,ids);int index=(int)((x-key_width)/key_width);if(fn)return FKEY+index;return index<n?ids[index]:0;}
    double start=controls_start();for(size_t i=0;i<sizeof controls/sizeof controls[0];i++){if(x>=start&&x<start+controls[i].width)return controls[i].action;start+=controls[i].width;}
    return 0;
}
static int key_for(int action){
    if(action>=FKEY && action<FKEY+12)return fkeys[action-FKEY];
    switch(action){case ESCAPE:return KEY_ESC;default:return 0;}
}
static int slider_percent(double x){int value=(int)lround((x-520)/1030*100);return (int)fmax(slider_mode==1?1:0,fmin(100,value));}
static bool parse_levels(const char *message,int values[3],int *is_muted){
    int b,v,k,m;char extra;
    if(sscanf(message,"LEVELS %d %d %d %d %c",&b,&v,&k,&m,&extra)!=4)return false;
    if(b< -1||b>100||v< -1||v>100||k< -1||k>100||m<0||m>1)return false;
    values[0]=b;values[1]=v;values[2]=k;*is_muted=m;return true;
}
static bool parse_market(const char *message){
    int index,direction,offset=0;
    if(sscanf(message,"MARKET %d %d%n",&index,&direction,&offset)!=2||index<0||index>=8||direction< -1||direction>2||message[offset]!='\t')return false;
    const unsigned char *p=(const unsigned char *)message+offset+1;
    MarketQuote next={.direction=direction,.received=true};
    char *fields[]={next.value,next.change,next.stamp};int limits[]={48,32,48};
    int field=0,length=0;
    for(;;p++){
        if(*p=='\t'||!*p){
            if(!length)return false;
            if(!*p){if(field!=2)return false;break;}
            if(++field>=3)return false;
            length=0;continue;
        }
        if(*p<32||*p>126||length>=limits[field]-1)return false;
        fields[field][length++]=(char)*p;
    }
    market_quotes[index]=next;return true;
}
static bool parse_wifi_details(const char *message){
    if(strncmp(message,"WIFIINFO ",9))return false;
    char next[9][128]={{0}};int field=0,length=0;
    for(const unsigned char *p=(const unsigned char *)message+9;;p++){
        if(*p=='\t'||!*p){
            if(!length)return false;
            if(!*p){if(field!=8)return false;break;}
            if(++field>=9)return false;
            length=0;continue;
        }
        if(*p<32||*p==127||length>=127)return false;
        next[field][length++]=(char)*p;
    }
    memcpy(wifi_details,next,sizeof next);return true;
}
static const char *wifi_detail(int index){return wifi_details[index][0]?wifi_details[index]:"—";}
static bool parse_battery_details(const char *message){
    if(strncmp(message,"BATINFO ",8))return false;
    char next[8][48]={{0}};int field=0,length=0;
    for(const unsigned char *p=(const unsigned char *)message+8;;p++){
        if(*p=='\t'||!*p){
            if(!length)return false;
            if(!*p){if(field!=7)return false;break;}
            if(++field>=8)return false;
            length=0;continue;
        }
        if(*p<32||*p>126||length>=47)return false;
        next[field][length++]=(char)*p;
    }
    memcpy(battery_details,next,sizeof next);return true;
}
static bool parse_nightlight(const char *message,int *enabled){
    int value;char extra;
    if(sscanf(message,"NIGHTLIGHT %d %c",&value,&extra)!=1||value< -1||value>1)return false;
    *enabled=value;return true;
}
static bool parse_aux(const char *message,int *mic,int *play){
    int m,p;char extra;
    if(sscanf(message,"AUX %d %d %c",&m,&p,&extra)!=2||m< -1||m>1||p< -1||p>1)return false;
    *mic=m;*play=p;return true;
}
static int media_request(int bridge,const char *action,int value){
    char request[32];int n=snprintf(request,sizeof request,"%s %c %d",action,media_kind[slider_mode-1],value);
    ssize_t sent=send(bridge,request,n,MSG_NOSIGNAL);
    if(sent==n)return 1;
    if(sent<0&&(errno==EAGAIN||errno==EWOULDBLOCK))return 0;
    return -1;
}
static bool parse_state(const char *message,State *s){
    int active,locked;unsigned occupied,present;char extra;
    if(sscanf(message,"STATE %d %u %u %d %c",&active,&occupied,&present,&locked,&extra)!=4)return false;
    if(active<0 || active>10 || occupied>2046 || present>2046 || (occupied&1) || (present&1) || locked<0 || locked>1)return false;
    *s=(State){active,occupied,present,locked};return true;
}
static void text(cairo_t *cr,const char *label,double x,double width,double size){
    cairo_text_extents_t e;cairo_set_font_size(cr,size);cairo_text_extents(cr,label,&e);
    cairo_move_to(cr,x+(width-e.width)/2-e.x_bearing,30-e.height/2-e.y_bearing);cairo_show_text(cr,label);
}
/* Exact glyphs from Omarchy's monitor/audio panels and keyboard OSD.
 * The main speaker follows mute state; red icons identify muted controls. */
static const char *control_icon(int action){
    switch(action){
        case OPEN_B:return "󰍹";
        case OPEN_V:return muted?"":"";
        case OPEN_K:return "󰌌";
        case MUTE:return "";
        case MIC_MUTE:return mic_muted==0?"󰍬":"󰍭";
        case WIFI:return !strcmp(wifi_detail(0),"wifi")?"󰤨":!strcmp(wifi_detail(0),"ethernet")?"󰈀":"󰤮";
        case NIGHTLIGHT:return "󰖔";
        case PLAYPAUSE:return playing==1?"󰏤":"󰐊";
        default:return NULL;
    }
}
static void control_symbol(cairo_t *cr,const char *glyph,double x,double width){
    cairo_save(cr);
    cairo_select_font_face(cr,"JetBrainsMono Nerd Font",CAIRO_FONT_SLANT_NORMAL,CAIRO_FONT_WEIGHT_NORMAL);
    cairo_font_options_t *options=cairo_font_options_create();
    cairo_get_font_options(cr,options);cairo_font_options_set_antialias(options,CAIRO_ANTIALIAS_GRAY);
    cairo_set_font_options(cr,options);cairo_font_options_destroy(options);
    /* Match visual ink height across the mixed Font Awesome / Material set. */
    cairo_set_font_size(cr,48);cairo_text_extents_t e;cairo_text_extents(cr,glyph,&e);
    double scale=fmin(24/fmax(1,e.height),(width-28)/fmax(1,e.width));
    cairo_set_font_size(cr,48*scale);cairo_text_extents(cr,glyph,&e);
    cairo_move_to(cr,round(x+(width-e.width)/2-e.x_bearing),round(30-e.height/2-e.y_bearing));
    cairo_show_text(cr,glyph);cairo_restore(cr);
}
/* Pre-compensate for the final light-mode inversion so warnings stay red. */
static void warning_colour(cairo_t *cr){
    double r=1,g=.22,b=.22;cairo_set_source_rgb(cr,light_mode?1-r:r,light_mode?1-g:g,light_mode?1-b:b);
}
static void selection_colour(cairo_t *cr,double brightness){
    /* A readable blue in each theme; compensate for finish_paint inversion. */
    double r=(light_mode?.12:.30)*brightness,g=(light_mode?.38:.60)*brightness,b=(light_mode?.70:.90)*brightness;
    cairo_set_source_rgb(cr,light_mode?1-r:r,light_mode?1-g:g,light_mode?1-b:b);
}
static void button_colour(cairo_t *cr,bool selected){
    if(selected)selection_colour(cr,1);else cairo_set_source_rgb(cr,.8,.85,.9);
}
static void mute_button(cairo_t *cr,int action,double x,double width,int pressed,bool active,bool unavailable,double brightness){
    double alpha=brightness*(unavailable?.4:1);
    if(active)warning_colour(cr);else if(pressed==action)selection_colour(cr,alpha);else cairo_set_source_rgb(cr,.78*alpha,.82*alpha,.88*alpha);
    control_symbol(cr,control_icon(action),x,width);
}
static void battery_label(cairo_t *cr){
    char label[16];if(battery_percent<0)strcpy(label,"—");else snprintf(label,sizeof label,"%d%%",battery_percent);
    if(battery_percent>=0&&battery_percent<20)warning_colour(cr);else if(battery_percent>=0&&battery_charging)selection_colour(cr,1);else cairo_set_source_rgb(cr,.8,.85,.9);
    text(cr,label,control_x(BATTERY),key_width,workspace_font_size);
}
static void theme_button(cairo_t *cr,int pressed){
    double x=control_x(THEME);
    cairo_new_path(cr);button_colour(cr,pressed==THEME);cairo_set_line_width(cr,2);cairo_arc(cr,x+key_width/2,30,11,0,2*M_PI);cairo_stroke(cr);
    cairo_arc(cr,x+key_width/2,30,9,-M_PI/2,M_PI/2);cairo_close_path(cr);cairo_fill(cr);
    button_colour(cr,pressed==WIFI||wifi_mode);control_symbol(cr,control_icon(WIFI),control_x(WIFI),key_width);
    battery_label(cr);
}
static const char *battery_detail(int index){return battery_details[index][0]?battery_details[index]:"—";}
static void battery_panel(cairo_t *cr,int pressed){
    const char *state=battery_detail(1),*status="Battery";
    bool discharging=!strcmp(state,"discharging"),holding=!strcmp(state,"holding"),full=!strcmp(state,"fully-charged");
    if(discharging)status="On battery";else if(!strcmp(state,"charging"))status="Charging";
    else if(holding)status="Holding charge";else if(full)status="Fully charged";
    else if(!strcmp(state,"pending-charge"))status="Not charging";
    else if(!strcmp(state,"pending-discharge"))status="Pending discharge";
    else if(!strcmp(state,"empty"))status="Empty";
    const char *profile=battery_detail(7);
    if(!strcmp(profile,"power-saver"))profile="Power Saver";
    else if(!strcmp(profile,"balanced"))profile="Balanced";
    else if(!strcmp(profile,"performance"))profile="Performance";
    const char *labels[]={status,discharging?"Time left":"Time to full",discharging?"Power draw":"Charge rate","Battery size","Charge cycles","Charge limit","Power profile · tap"};
    const char *values[]={battery_detail(0),(holding||full)?"—":battery_detail(4),(holding||full)?"—":battery_detail(2),battery_detail(3),battery_detail(5),battery_detail(6),profile};
    const int edges[]={80,345,610,875,1140,1405,1655,1930};
    button_colour(cr,pressed==BATTERY_BACK);text(cr,"Back",0,key_width,22);
    for(int i=0;i<7;i++){
        double x=edges[i],width=edges[i+1]-x;
        cairo_save(cr);cairo_rectangle(cr,x+8,0,width-16,60);cairo_clip(cr);
        cairo_set_source_rgb(cr,.55,.6,.65);cairo_save(cr);cairo_translate(cr,0,-15);text(cr,labels[i],x,width,17);cairo_restore(cr);
        button_colour(cr,i==6&&pressed==POWER_PROFILE);
        if(i==0){if(battery_percent>=0&&battery_percent<20)warning_colour(cr);else if(battery_charging)selection_colour(cr,1);}
        cairo_save(cr);cairo_translate(cr,0,12);text(cr,values[i],x,width,24);cairo_restore(cr);
        cairo_restore(cr);
    }
    theme_button(cr,pressed);
}
static void wifi_panel(cairo_t *cr,int pressed){
    const char *kind=wifi_detail(0);
    bool wifi=!strcmp(kind,"wifi"),ethernet=!strcmp(kind,"ethernet");
    const char *name=wifi||ethernet?wifi_detail(1):!strcmp(kind,"disconnected")?"Not connected":"Unavailable";
    const char *labels[]={wifi?"Wi-Fi network":ethernet?"Ethernet":"Network", "Link speed", "Signal", "Band", "Traffic ↓ / ↑", "IP address", "Ping: router / internet"};
    const char *values[]={name,wifi_detail(2),wifi_detail(3),wifi_detail(4),wifi_detail(5),wifi_detail(6),wifi_detail(7)};
    const int edges[]={80,480,710,890,1050,1330,1630,1930};
    button_colour(cr,pressed==WIFI_BACK);text(cr,"Back",0,key_width,22);
    for(int i=0;i<7;i++){
        double x=edges[i],width=edges[i+1]-x;
        cairo_save(cr);cairo_rectangle(cr,x+8,0,width-16,60);cairo_clip(cr);
        cairo_set_source_rgb(cr,.55,.6,.65);cairo_save(cr);cairo_translate(cr,0,-15);text(cr,labels[i],x,width,17);cairo_restore(cr);
        button_colour(cr,false);
        cairo_text_extents_t e;cairo_set_font_size(cr,24);cairo_text_extents(cr,values[i],&e);
        double size=e.width>width-20?fmax(17,24*(width-20)/e.width):24;
        cairo_save(cr);cairo_translate(cr,0,12);text(cr,values[i],x,width,size);cairo_restore(cr);
        cairo_restore(cr);
    }
    theme_button(cr,pressed);
}
static void market_colour(cairo_t *cr,int direction){
    if(direction==1){double r=light_mode?.05:.25,g=light_mode?.48:.85,b=light_mode?.18:.45;cairo_set_source_rgb(cr,light_mode?1-r:r,light_mode?1-g:g,light_mode?1-b:b);}
    else if(direction== -1)warning_colour(cr);else button_colour(cr,false);
}
static void markets_button(cairo_t *cr,int pressed){
    button_colour(cr,pressed==MARKETS||markets_mode);
    control_symbol(cr,"󰄧",8*key_width,key_width);
}
static void markets_panel(cairo_t *cr,int pressed){
    const char *labels[]={"Gold USD/oz","Silver USD/oz","ASX 200 pts","S&P 500 pts","Bitcoin USD","A$1 → EUR","A$1 → USD","A$1 → JPY"};
    button_colour(cr,pressed==MARKETS_BACK);text(cr,"Back",0,key_width,22);
    for(int i=0;i<8;i++){
        double x=key_width+floor(i*(control_x(THEME)-key_width)/8),end=key_width+floor((i+1)*(control_x(THEME)-key_width)/8),width=end-x;
        MarketQuote *quote=&market_quotes[i];
        const char *value=quote->received?quote->value:"—",*change=quote->received?quote->change:"Loading",*stamp=quote->received?quote->stamp:"Delayed quotes";
        cairo_save(cr);cairo_rectangle(cr,x+5,0,width-10,60);cairo_clip(cr);
        button_colour(cr,false);cairo_save(cr);cairo_translate(cr,0,-20);text(cr,labels[i],x,width,16);cairo_restore(cr);
        market_colour(cr,quote->received?quote->direction:2);text(cr,value,x,width,21);
        char detail[96];snprintf(detail,sizeof detail,"%s · %s",change,stamp);
        cairo_save(cr);cairo_translate(cr,0,20);text(cr,detail,x,width,12);cairo_restore(cr);
        cairo_restore(cr);
    }
    theme_button(cr,pressed);
}
static void finish_paint(cairo_t *cr,cairo_surface_t *surface){
    if(light_mode){cairo_identity_matrix(cr);cairo_set_operator(cr,CAIRO_OPERATOR_DIFFERENCE);cairo_set_source_rgb(cr,1,1,1);cairo_paint(cr);}
    cairo_destroy(cr);cairo_surface_flush(surface);
}
static void app_button(cairo_t *cr,int pressed){
    double x=6*key_width;bool feedback=pressed==CHATGPT||now()<app_feedback_until;
    button_colour(cr,feedback);
    if(chatgpt_icon){
        const double size=32;
        cairo_save(cr);cairo_translate(cr,x+(key_width-size)/2,(60-size)/2);cairo_scale(cr,size/cairo_image_surface_get_width(chatgpt_icon),size/cairo_image_surface_get_height(chatgpt_icon));cairo_mask_surface(cr,chatgpt_icon,0,0);cairo_restore(cr);
    }else{
        cairo_set_line_width(cr,2);cairo_rectangle(cr,x+31,17,38,26);cairo_stroke(cr);cairo_move_to(cr,x+38,43);cairo_line_to(cr,x+33,50);cairo_line_to(cr,x+48,43);cairo_stroke(cr);
    }
}
static void files_icon(cairo_t *cr,double x,double width){
    double left=round(x+width/2)-14;
    cairo_new_path(cr);cairo_set_line_width(cr,2);
    cairo_move_to(cr,left,20);cairo_line_to(cr,left+10,20);
    cairo_line_to(cr,left+14,24);cairo_line_to(cr,left+28,24);
    cairo_line_to(cr,left+28,40);cairo_line_to(cr,left,40);
    cairo_close_path(cr);cairo_move_to(cr,left,26);cairo_line_to(cr,left+28,26);cairo_stroke(cr);
}
static void files_panel(cairo_t *cr,int pressed){
    button_colour(cr,pressed==FILES_BACK);text(cr,"Back",0,key_width,22);
    button_colour(cr,false);files_icon(cr,100,80);
    for(int i=0;i<7;i++){
        button_colour(cr,pressed==FILES_FOLDER+i);
        text(cr,files_labels[i],240+i*260,260,workspace_font_size);
    }
}
#include "news-view.h"

static void timer_icon(cairo_t *cr,double x,double width){
    /* Native pixel geometry: 24 x 28 including the two-pixel stroke.
     * Integer centres and an even stroke keep straight edges pixel-aligned. */
    double cx=round(x+width/2);
    cairo_new_path(cr);
    cairo_set_line_width(cr,2);cairo_arc(cr,cx,32,11,0,2*M_PI);cairo_stroke(cr);
    cairo_move_to(cr,cx,24);cairo_line_to(cr,cx,32);cairo_line_to(cr,cx+5,32);
    cairo_move_to(cr,cx-4,17);cairo_line_to(cr,cx+4,17);
    cairo_move_to(cr,cx,17);cairo_line_to(cr,cx,21);cairo_stroke(cr);
}
static void timer_label(cairo_t *cr,double x,double width){
    int remaining=pomodoro_remaining(timer_now());
    if(remaining<=0)selection_colour(cr,1);
    else if(pomodoro_warning(remaining))warning_colour(cr);
    else button_colour(cr,false);
    int seconds=abs(remaining);char label[32];
    snprintf(label,sizeof label,"%02d:%02d",seconds/60,seconds%60);
    text(cr,label,x,width,workspace_font_size);
}
static void timer_panel(cairo_t *cr,int pressed){
    button_colour(cr,pressed==TIMER_BACK);text(cr,"Back",0,key_width,22);
    button_colour(cr,false);timer_icon(cr,100,80);
    for(int i=0;i<4;i++){
        char label[16];snprintf(label,sizeof label,"%d min",timer_presets[i]);
        button_colour(cr,pressed==TIMER_PRESET+i);text(cr,label,240+i*160,160,workspace_font_size);
    }
    if(pomodoro_deadline>0)timer_label(cr,1000,200);
    button_colour(cr,pressed==TIMER_PAUSE);
    if(pomodoro_deadline<=0)cairo_set_source_rgb(cr,.3,.32,.35);
    text(cr,timer_paused?"Resume":"Pause",1240,160,workspace_font_size);
    button_colour(cr,pressed==TIMER_CLEAR);
    if(pomodoro_deadline<=0)cairo_set_source_rgb(cr,.3,.32,.35);
    text(cr,"Clear",1440,160,workspace_font_size);
}
#include "weather-view.h"
static void piano_colour(cairo_t *cr,double r,double g,double b){
    cairo_set_source_rgb(cr,light_mode?1-r:r,light_mode?1-g:g,light_mode?1-b:b);
}
static void piano_icon(cairo_t *cr,int pressed){
    button_colour(cr,pressed==PIANO);double x=9*key_width+22;
    cairo_set_line_width(cr,2);cairo_rectangle(cr,x,17,36,26);cairo_stroke(cr);
    for(int i=1;i<5;i++){cairo_move_to(cr,x+i*7,30);cairo_line_to(cr,x+i*7,43);}cairo_stroke(cr);
    for(int i=0;i<4;i++)if(i!=1){cairo_rectangle(cr,x+5+i*7,18,4,14);cairo_fill(cr);}
}
static int piano_control_hit(double x){
    if(x<0||x>=2170)return 0;
    if(x<80)return PIANO_BACK;
    if(piano_demo_menu){if(x>=2080)return PIANO_PAGE;return x>=240&&x<2000?PIANO_SONG+piano_demo_page*4+(int)((x-240)/440):0;}
    if(x<160)return PIANO_DEMO;
    if(piano_demo_playing)return 0;
    if(x<240)return PIANO_DOWN;
    if(x<320)return PIANO_UP;
    return x<400?PIANO_SUSTAIN:0;
}
static bool piano_hold_due(double time){
    return piano_contact&&piano_touch_action==PIANO_DEMO&&!piano_hold_handled&&time-piano_touch_since>=.65;
}
static bool piano_command(int bridge,const char *request){
    return send(bridge,request,strlen(request),MSG_NOSIGNAL)==(ssize_t)strlen(request);
}
static void piano_demo_icon(cairo_t *cr){
    if(piano_status<0)warning_colour(cr);else button_colour(cr,piano_demo_playing||piano_touch_action==PIANO_DEMO);
    if(piano_demo_playing){cairo_rectangle(cr,110,20,20,20);cairo_fill(cr);return;}
    /* Native vector music note; no font dependency or text label. */
    cairo_set_line_width(cr,3);cairo_move_to(cr,123,37);cairo_line_to(cr,123,16);
    cairo_line_to(cr,137,20);cairo_line_to(cr,137,27);cairo_line_to(cr,123,23);cairo_stroke(cr);
    cairo_save(cr);cairo_translate(cr,116,39);cairo_scale(cr,1.5,1);cairo_arc(cr,0,0,5,0,2*M_PI);cairo_fill(cr);cairo_restore(cr);
}
static void piano_panel(cairo_t *cr){
    button_colour(cr,piano_back_contact);text(cr,"Back",0,80,22);
    if(piano_demo_menu){
        for(int i=0;i<4;i++){
            int song=piano_demo_page*4+i;
            button_colour(cr,song==piano_demo_index||piano_touch_action==PIANO_SONG+song);
            text(cr,piano_demo_names[song],240+i*440,440,24);
        }
        button_colour(cr,piano_touch_action==PIANO_PAGE);text(cr,piano_demo_page?"2/2 ‹":"1/2 ›",2080,90,18);
        return;
    }
    piano_demo_icon(cr);
    button_colour(cr,piano_touch_action==PIANO_DOWN);
    if(piano_octave<=-2||piano_demo_playing)piano_colour(cr,.3,.32,.35);
    text(cr,"8−",160,80,22);
    button_colour(cr,piano_touch_action==PIANO_UP);
    if(piano_octave>=2||piano_demo_playing)piano_colour(cr,.3,.32,.35);
    text(cr,"8+",240,80,22);
    button_colour(cr,piano_sustain||piano_touch_action==PIANO_SUSTAIN);
    if(piano_demo_playing)piano_colour(cr,.3,.32,.35);
    /* Pedal outline: blue while sustain is enabled. */
    cairo_set_line_width(cr,2);cairo_move_to(cr,346,40);cairo_line_to(cr,350,21);
    cairo_line_to(cr,370,21);cairo_line_to(cr,374,40);cairo_close_path(cr);cairo_stroke(cr);
    cairo_move_to(cr,350,35);cairo_line_to(cr,370,35);cairo_stroke(cr);
    unsigned visual=piano_demo_playing?piano_demo_mask:piano_mask;
    for(int i=0;i<15;i++){
        double x=400+i*118;
        if(visual&(1u<<piano_white[i]))selection_colour(cr,1);else piano_colour(cr,.94,.95,.97);
        cairo_rectangle(cr,x+1,1,116,58);cairo_fill(cr);
        piano_colour(cr,.2,.22,.26);char label[16];const char *names[]={"C","D","E","F","G","A","B"};
        if(i%7==0)snprintf(label,sizeof label,"C%d",4+i/7+(piano_demo_playing?0:piano_octave));else snprintf(label,sizeof label,"%s",names[i%7]);
        cairo_set_font_size(cr,13);cairo_move_to(cr,x+48,54);cairo_show_text(cr,label);
    }
    for(int i=0;i<14;i++){
        if(piano_white[i+1]-piano_white[i]!=2)continue;
        if(visual&(1u<<(piano_white[i]+1)))selection_colour(cr,1);else piano_colour(cr,.06,.07,.09);
        cairo_rectangle(cr,400+(i+1)*118-33,0,66,35);cairo_fill(cr);
    }
}
static void paint(cairo_surface_t *surface,int width,int height,State state,bool fn,double marker,int pressed,bool dim){
    (void)marker; /* Focus is shown on the actual active number, without a box. */
    cairo_t *cr=cairo_create(surface);cairo_set_source_rgb(cr,0,0,0);cairo_paint(cr);
    if(height>width){cairo_matrix_t rotation;cairo_matrix_init(&rotation,0,1,-1,0,width,0);cairo_transform(cr,&rotation);}
    cairo_select_font_face(cr,"sans",CAIRO_FONT_SLANT_NORMAL,CAIRO_FONT_WEIGHT_NORMAL);
    if(calibration_mode){
        cairo_set_source_rgb(cr,.8,.85,.9);text(cr,"A",0,250,42);text(cr,"B",1920,250,42);
        text(cr,"CALIBRATION: TAP A, THEN B",250,1670,30);
        if(calibration_x>=0){cairo_set_source_rgb(cr,.3,.6,.9);cairo_rectangle(cr,calibration_x-3,0,6,60);cairo_fill(cr);}
        finish_paint(cr,surface);return;
    }
    if(piano_mode){piano_panel(cr);finish_paint(cr,surface);return;}
    if(news_mode&&!battery_mode){news_panel(cr,pressed);finish_paint(cr,surface);return;}
    if(files_menu){files_panel(cr,pressed);finish_paint(cr,surface);return;}
    if(timer_menu){timer_panel(cr,pressed);finish_paint(cr,surface);return;}
    if(markets_mode){markets_panel(cr,pressed);finish_paint(cr,surface);return;}
    if(wifi_mode){wifi_panel(cr,pressed);finish_paint(cr,surface);return;}
    if(battery_mode){battery_panel(cr,pressed);finish_paint(cr,surface);return;}
    if(weather_mode){weather_paint(cr,pressed);finish_paint(cr,surface);return;}
    double brightness=dim?.35:1;
    if(slider_mode){
        int value=levels[slider_mode-1];const char *names[]={"Display","Volume","Keyboard"};char title[64];
        if(value<0)snprintf(title,sizeof title,"%s —",names[slider_mode-1]);
        else snprintf(title,sizeof title,"%s %d%%",names[slider_mode-1],value);
        button_colour(cr,pressed==ESCAPE);text(cr,"esc",0,key_width,27);button_colour(cr,false);text(cr,title,100,290,26);
        button_colour(cr,pressed==MINUS);text(cr,"−",390,100,38);button_colour(cr,pressed==PLUS);text(cr,"+",1580,100,38);button_colour(cr,pressed==DONE);text(cr,"Done",slider_mode==2?1780:1680,100,28);
        cairo_set_line_cap(cr,CAIRO_LINE_CAP_ROUND);cairo_set_line_width(cr,7);cairo_set_source_rgb(cr,.2,.24,.3);
        cairo_move_to(cr,520,30);cairo_line_to(cr,1550,30);cairo_stroke(cr);
        if(value>=0){double knob=520+1030*value/100.0;selection_colour(cr,1);cairo_move_to(cr,520,30);cairo_line_to(cr,knob,30);cairo_stroke(cr);cairo_arc(cr,knob,30,13,0,2*M_PI);cairo_fill(cr);}
        if(slider_mode==2)mute_button(cr,MUTE,1680,100,pressed,muted,false,1);
        theme_button(cr,pressed);finish_paint(cr,surface);return;
    }
    int ids[10],n=ids_for(state,ids);double step=key_width;
    if(state.locked){cairo_set_source_rgb(cr,.65*brightness,.7*brightness,.78*brightness);text(cr,"Locked",100,1200,30);}
    for(int i=0;i<(state.locked?0:(fn?12:n));i++){
        int action=fn?FKEY+i:ids[i];bool occupied=!fn&&(state.occupied&(1u<<ids[i]));
        double alpha=(fn||occupied||state.active==action)?brightness:brightness*.5;
        int slot=fn?i:workspace_slot(i);
        step=!fn&&i>=5?workspace_step():key_width;
        double left=!fn&&i>=5?optional_end()+(i-5)*step:key_width+slot*step;
        if(pressed==action||(!fn&&state.active==action))selection_colour(cr,brightness);else cairo_set_source_rgb(cr,.82*alpha,.86*alpha,.92*alpha);char label[16];
        if(fn)snprintf(label,sizeof label,"F%d",i+1);else snprintf(label,sizeof label,"%d",ids[i]);
        text(cr,label,left,step,fn?22:workspace_font_size);
        if(occupied){cairo_arc(cr,left+step*.5,53,2,0,2*M_PI);cairo_fill(cr);}
    }
    if(!state.locked&&!fn){
        double left=480;for(int i=0;i<6;i++)if(optional_on[i]){
            cairo_save(cr);cairo_translate(cr,left-(480+80*i),0);
            switch(optional_actions[i]){
                case CHATGPT:app_button(cr,pressed);break;case WEATHER:weather_button(cr,pressed);break;
                case MARKETS:markets_button(cr,pressed);break;case PIANO:piano_icon(cr,pressed);break;
                case FILES:button_colour(cr,pressed==FILES);files_icon(cr,800,key_width);break;
                case NEWS:button_colour(cr,pressed==NEWS);news_icon(cr,880);break;
            }cairo_restore(cr);left+=80;
        }
    }
    if(pressed==ESCAPE)selection_colour(cr,brightness);else cairo_set_source_rgb(cr,.78*brightness,.82*brightness,.88*brightness);text(cr,"esc",0,key_width,27);
    double start=controls_start();
    for(size_t i=0;i<sizeof controls/sizeof controls[0];i++){
        int action=controls[i].action;bool active=(action==OPEN_V&&muted)||(action==MIC_MUTE&&mic_muted==1);
        bool unavailable=(action==MIC_MUTE&&mic_muted<0)||(action==PLAYPAUSE&&playing<0)||(action==NIGHTLIGHT&&nightlight_enabled<0);
        const char *label=action==TOGGLE?(fn?"WS":"Fn"):controls[i].label;
        if(action==THEME||action==WIFI||action==BATTERY){start+=controls[i].width;continue;}
        if(action==OPEN_V||action==MIC_MUTE){
            mute_button(cr,action,start,controls[i].width,pressed,active,unavailable,brightness);
            if(unavailable&&action==MIC_MUTE){cairo_select_font_face(cr,"sans",CAIRO_FONT_SLANT_NORMAL,CAIRO_FONT_WEIGHT_NORMAL);cairo_set_font_size(cr,14);cairo_move_to(cr,start+controls[i].width-18,53);cairo_show_text(cr,"?");}
            start+=controls[i].width;continue;
        }
        double alpha=brightness*(unavailable?.4:1);
        if(pressed==action||(action==TOGGLE&&fn)||(action==PLAYPAUSE&&playing==1)||(action==NIGHTLIGHT&&nightlight_enabled==1))selection_colour(cr,alpha);else cairo_set_source_rgb(cr,.78*alpha,.82*alpha,.88*alpha);
        if(action==POMODORO){
            timer_icon(cr,start,key_width);
            if(pomodoro_deadline>0)timer_label(cr,start+key_width,key_width);
            start+=controls[i].width;continue;
        }
        const char *icon=control_icon(action);
        if(icon)control_symbol(cr,icon,start,controls[i].width);else text(cr,label,start,controls[i].width,22);
        if(active){cairo_arc(cr,start+controls[i].width/2,53,2,0,2*M_PI);cairo_fill(cr);}
        if(unavailable&&action==MIC_MUTE){cairo_select_font_face(cr,"sans",CAIRO_FONT_SLANT_NORMAL,CAIRO_FONT_WEIGHT_NORMAL);cairo_set_font_size(cr,14);cairo_move_to(cr,start+controls[i].width-18,53);cairo_show_text(cr,"?");}
        start+=controls[i].width;
    }
    theme_button(cr,pressed);finish_paint(cr,surface);
}
static bool emit_key(int fd,int key,int value){
    struct input_event events[2]={{.type=EV_KEY,.code=key,.value=value},{.type=EV_SYN,.code=SYN_REPORT}};
    return write(fd,events,sizeof events)==sizeof events;
}
static int make_keyboard(void){
    int fd=open("/dev/uinput",O_WRONLY|O_NONBLOCK|O_CLOEXEC);if(fd<0)return -1;
    if(ioctl(fd,UI_SET_EVBIT,EV_KEY))return -1;
    int keys[]={KEY_ESC,KEY_MUTE,KEY_VOLUMEDOWN,KEY_VOLUMEUP,KEY_BRIGHTNESSDOWN,KEY_BRIGHTNESSUP};
    for(size_t i=0;i<sizeof keys/sizeof keys[0];i++)if(ioctl(fd,UI_SET_KEYBIT,keys[i]))return -1;
    for(int i=0;i<12;i++)if(ioctl(fd,UI_SET_KEYBIT,fkeys[i]))return -1;
    struct uinput_setup setup={.id={.bustype=BUS_VIRTUAL,.vendor=0x05ac,.product=0x8601,.version=1}};
    strcpy(setup.name,"Omarchy Touch Bar Controls");
    if(ioctl(fd,UI_DEV_SETUP,&setup)||ioctl(fd,UI_DEV_CREATE))return -1;
    return fd;
}
static double marker_for(State s){int ids[10],n=ids_for(s,ids);for(int i=0;i<n;i++)if(ids[i]==s.active)return workspace_slot(i);return -1;}
static bool touch_packet(const unsigned char *data,size_t size,bool *down,double *x,bool *multi){
    if(size!=52)return false;
    int contacts=0;double position=0;
    for(int i=0;i<10;i++){
        const unsigned char *r=data+4*i;if(!(r[0]&0x10))continue;
        unsigned raw=r[1]|((unsigned)r[2]<<8);if(raw>32767||r[3]>127)return false;
        if(!contacts)position=2170.0*raw/32767.0;
        contacts++;
    }
    *down=contacts>0;*multi=contacts>1;*x=position;return true;
}
/* Native 2170 x 60 coordinates: 15 white keys, C4 through C6. */
static int piano_hit(double x,double y){
    if(x<400||x>=2170||y<0||y>=60)return -1;
    if(y<35)for(int i=0;i<14;i++){
        if(piano_white[i+1]-piano_white[i]!=2)continue;
        double centre=400+(i+1)*118;
        if(x>=centre-33&&x<centre+33)return piano_white[i]+1;
    }
    return piano_white[(int)((x-400)/118)];
}
static unsigned piano_report(const unsigned char *data,int *contacts,bool *back){
    unsigned mask=0;*contacts=0;*back=false;
    for(int i=0;i<10;i++){
        const unsigned char *r=data+4*i;if(!(r[0]&0x10))continue;
        unsigned raw=r[1]|((unsigned)r[2]<<8);
        if(raw>32767||r[3]>127)continue;
        double x=2170.0*raw/32767.0,y=60.0*r[3]/128.0;
        (*contacts)++;if(x<80)*back=true;
        int note=piano_hit(x,y);if(note>=0)mask|=1u<<note;
    }
    *back=*back&&*contacts==1;return mask;
}
static bool piano_send(int bridge,unsigned mask){
    char request[48];int n=snprintf(request,sizeof request,"PIANO NOTES %u",mask);
    if(send(bridge,request,n,MSG_NOSIGNAL)!=n)return false;
    piano_mask=mask;piano_last_send=now();return true;
}
static void self_test(bool sockets){
    configure_widths();
    State news_state={0};
    assert(hit(880,false,news_state)==NEWS&&hit(959.99,false,news_state)==NEWS);
    news_state.locked=true;assert(hit(880,false,news_state)==0);news_state.locked=false;
    news_mode=true;news_menu=true;
    assert(hit(0,false,news_state)==NEWS_EXIT&&hit(200,false,news_state)==NEWS_AI&&hit(359.99,false,news_state)==NEWS_AI&&hit(360,false,news_state)==0&&hit(400,false,news_state)==NEWS_GLOBAL&&hit(599.99,false,news_state)==NEWS_GLOBAL&&hit(600,false,news_state)==0&&hit(2169,false,news_state)==BATTERY);
    news_menu=false;assert(hit(80,false,news_state)==NEWS_HEADLINE&&hit(2089,false,news_state)==NEWS_HEADLINE);
    news_state.locked=true;assert(hit(100,false,news_state)==0);news_state.locked=false;
    assert(parse_news("NEWS 0 1 0123456789abcdef 1234567890 1 3\tExample headline"));
    assert(!parse_news("NEWS 0 1 bad 0 1 3\tBad token"));
    assert(!parse_news("NEWS 2 1 0123456789abcdef 0 1 3\tBad category"));
    assert(!parse_news("NEWS 0 1 0123456789abcdef 0 4 3\tBad position"));
    assert(!parse_news("NEWS 0 1 0123456789abcdef 0 1 3\tInvalid\nheadline"));
    assert(!news_utf8((const unsigned char *)"\xf0\x80\x80\x80"));
    assert(!news_utf8((const unsigned char *)"\xe2\x82"));
    news_frozen=true;assert(parse_news("NEWS 1 2 fedcba9876543210 0 2 3\tUpdated headline"));
    assert(!strcmp(news_report.token,"0123456789abcdef")&&news_pending_valid&&!strcmp(news_pending.token,"fedcba9876543210"));
    news_frozen=false;news_pending_valid=false;
    assert(news_hold_due(NEWS_HEADLINE,true,false,false,.65)&&!news_hold_due(NEWS_HEADLINE,true,true,false,1));
    news_report.count=3;
    news_touch_begin(1000);news_touch_move(880);assert(news_swipe(false,false)==1);
    assert(news_swipe(true,false)==0&&news_swipe(false,true)==0);
    assert(!news_hold_due(NEWS_HEADLINE,true,false,false,1));
    news_touch_begin(1000);news_touch_move(1120);assert(news_swipe(false,false)==-1);
    news_touch_begin(1000);news_touch_move(1018);assert(!news_touch_moved()&&news_swipe(false,false)==0);
    news_touch_move(1019);assert(news_touch_moved()&&news_swipe(false,false)==0);
    news_touch_move(1000);assert(news_touch_moved()&&news_swipe(false,false)==0);
    news_touch_begin(1000);assert(news_hold_due(NEWS_HEADLINE,true,false,false,.65));
    news_report.count=0;news_touch_move(800);assert(news_swipe(false,false)==0);news_touch_begin(0);
    news_overflow=0;assert(news_dwell()==10);news_overflow=600;assert(news_dwell()==16);news_overflow=0;
    news_frozen=true;news_started=10;news_frozen_at=15;assert(news_elapsed()==5);news_frozen=false;
    battery_mode=true;assert(hit(0,false,news_state)==BATTERY_BACK);battery_mode=false;
    news_mode=false;news_menu=false;news_report=(NewsReport){0};
    State files_state={0};
    assert(hit(800,false,files_state)==FILES&&hit(879.99,false,files_state)==FILES);
    files_state.locked=true;assert(hit(800,false,files_state)==0);
    files_menu=true;
    assert(hit(0,false,files_state)==0);
    files_state.locked=false;assert(hit(79.99,false,files_state)==FILES_BACK);
    assert(hit(80,false,files_state)==0&&hit(239.99,false,files_state)==0);
    for(int i=0;i<7;i++){
        assert(hit(240+260*i,false,files_state)==FILES_FOLDER+i);
        assert(hit(499.99+260*i,false,files_state)==FILES_FOLDER+i);
    }
    assert(hit(2060,false,files_state)==0&&hit(2169,false,files_state)==0);
    files_menu=false;
    assert(files_navigate(FILES)&&files_menu);
    assert(!files_navigate(PIANO)&&files_menu);
    assert(hit(240,false,files_state)==FILES_FOLDER); /* Home stays in the menu. */
    assert(files_navigate(FILES_BACK)&&!files_menu);

    if(sockets){
    int pair[2];assert(socketpair(AF_UNIX,SOCK_SEQPACKET,0,pair)==0);
    if(send(pair[0],"probe",5,MSG_NOSIGNAL)!=5){
        perror("Local socket self-test unavailable in this execution environment");
        close(pair[0]);close(pair[1]);exit(77);
    }
    char probe[8];assert(recv(pair[1],probe,sizeof probe,0)==5);
    char packet[64];
    for(int i=0;i<7;i++){
        assert(files_open(pair[0],i));ssize_t size=recv(pair[1],packet,sizeof packet-1,0);assert(size>0);packet[size]=0;
        char expected[64];snprintf(expected,sizeof expected,"FILES OPEN %s",files_targets[i]);assert(!strcmp(packet,expected));
    }
    assert(!files_open(pair[0],-1)&&!files_open(pair[0],7));
    assert(news_action(pair[0],NEWS,false)&&news_mode&&news_menu);
    assert(news_action(pair[0],NEWS_GLOBAL,false)&&!news_menu&&news_report.category==1);
    ssize_t received=recv(pair[1],packet,sizeof packet-1,0);assert(received>0);packet[received]=0;assert(!strcmp(packet,"NEWS SELECT 1"));
    news_report.count=1;strcpy(news_report.token,"0123456789abcdef");
    assert(news_action(pair[0],NEWS_HEADLINE,false));received=recv(pair[1],packet,sizeof packet-1,0);assert(received>0);packet[received]=0;assert(!strcmp(packet,"NEWS ARTICLE 0123456789abcdef"));
    assert(news_action(pair[0],NEWS_EXIT,false)&&!news_mode);received=recv(pair[1],packet,sizeof packet-1,0);assert(received>0);packet[received]=0;assert(!strcmp(packet,"NEWS CLOSE"));
    news_menu=false;news_report=(NewsReport){0};
    close(pair[0]);close(pair[1]);
    }
    assert(piano_control_hit(79)==PIANO_BACK&&piano_control_hit(80)==PIANO_DEMO&&piano_control_hit(159)==PIANO_DEMO&&piano_control_hit(160)==PIANO_DOWN&&piano_control_hit(240)==PIANO_UP&&piano_control_hit(320)==PIANO_SUSTAIN&&piano_control_hit(400)==0);
    piano_demo_menu=true;
    for(int i=0;i<4;i++){assert(piano_control_hit(240+i*440)==PIANO_SONG+i);assert(piano_control_hit(679.99+i*440)==PIANO_SONG+i);}
    assert(piano_control_hit(2000)==0&&piano_control_hit(2080)==PIANO_PAGE);
    piano_demo_page=1;for(int i=0;i<4;i++)assert(piano_control_hit(240+i*440)==PIANO_SONG+4+i);
    piano_demo_page=0;piano_demo_menu=false;
    piano_contact=true;piano_touch_action=PIANO_DEMO;piano_touch_since=10;
    assert(!piano_hold_due(10.64)&&piano_hold_due(10.66));piano_hold_handled=true;assert(!piano_hold_due(11));
    piano_contact=false;piano_hold_handled=false;piano_touch_action=0;
    for(int i=0;i<15;i++)assert(piano_hit(400+i*118+59,50)==piano_white[i]);
    assert(piano_hit(518,10)==1&&piano_hit(518,50)==2&&piano_hit(400,0)==0);
    assert(piano_hit(2169,59)==24&&piano_hit(2170,20)==-1&&piano_hit(399,20)==-1);
    assert(piano_hit(754,10)==5); /* E/F has no black key. */
    unsigned char chord[52]={0};
    chord[0]=chord[4]=0x10;unsigned first=(unsigned)(450*32767.0/2170),second=(unsigned)(1250*32767.0/2170);
    chord[1]=first&255;chord[2]=first>>8;chord[3]=110;chord[5]=second&255;chord[6]=second>>8;chord[7]=110;
    int count;bool back;assert(piano_report(chord,&count,&back)==((1u<<0)|(1u<<12))&&count==2&&!back);
    piano_demo_playing=true;assert(piano_control_hit(160)==0&&piano_control_hit(320)==0&&piano_control_hit(80)==PIANO_DEMO);piano_demo_playing=false;
    piano_mode=true;assert(hit(0,false,(State){0})==PIANO_BACK);piano_mode=false;
    for(int i=0;i<4;i++){
        timer_menu=true;assert(timer_select(TIMER_PRESET+i,100)&&!timer_menu);
        assert(pomodoro_remaining(100)==timer_presets[i]*60);
    }
    assert(controls[1].width==160&&controls_start()==1210);
    State all={.active=10,.occupied=2046,.present=2046};
    for(int i=5;i<10;i++)assert(hit(960+(i-5+.5)*workspace_step(),false,all)==i+1);
    assert(hit(controls_start(),false,all)==TOGGLE);
    double nightlight_x=control_x(NIGHTLIGHT);
    timer_control(TIMER_CLEAR,100);
    assert(controls[1].width==80&&controls_start()==1290);
    assert(control_x(NIGHTLIGHT)==nightlight_x&&control_x(NIGHTLIGHT)==control_x(POMODORO)+80);
    assert(!timer_select(TIMER_BACK,100));
    timer_select(TIMER_PRESET+3,100);
    assert(pomodoro_remaining(1300)==300&&!pomodoro_warning(300));
    assert(pomodoro_remaining(1301)==299&&pomodoro_warning(299));
    assert(pomodoro_remaining(1599)==1);
    assert(pomodoro_remaining(1600)==0&&!pomodoro_warning(0));
    assert(pomodoro_remaining(1601)==-1&&pomodoro_remaining(1665)==-65);
    assert(timer_control(TIMER_PAUSE,1665.25)&&timer_paused);
    assert(pomodoro_remaining(9000)==-65);
    assert(timer_control(TIMER_PAUSE,9000)&&!timer_paused);
    assert(pomodoro_remaining(9001)==-66);
    assert(timer_control(TIMER_CLEAR,9001)&&pomodoro_deadline==0&&!timer_paused);
    assert(!timer_control(TIMER_PAUSE,9002)&&!timer_control(TIMER_CLEAR,9002));
    timer_select(TIMER_PRESET,100);
    assert(timer_control(TIMER_PAUSE,200.5)&&pomodoro_remaining(9000)==200);
    timer_control(TIMER_PAUSE,9000);assert(pomodoro_remaining(9000)==200&&pomodoro_remaining(9001)==199);
    timer_control(TIMER_PAUSE,9001);timer_select(TIMER_PRESET+1,9100);
    assert(!timer_paused&&pomodoro_remaining(9100)==600);
    timer_control(TIMER_CLEAR,9100);
    State s={.active=5,.occupied=32,.present=32};
    assert(hit(0,false,s)==ESCAPE&&hit(80,false,s)==1&&hit(5*key_width+.5,false,s)==5&&hit(720,false,s)==PIANO);
    assert(hit(480,false,s)==CHATGPT&&hit(controls_start(),false,s)==TOGGLE&&hit(2169,false,s)==BATTERY&&hit(2170,false,s)==0);
    assert(hit(80,true,s)==FKEY&&hit(1039,true,s)==FKEY+11&&hit(1040,true,s)==0);
    s.locked=true;assert(hit(480,false,s)==0&&hit(controls_start(),false,s)==0&&hit(0,false,s)==ESCAPE&&hit(control_x(OPEN_B)+50,false,s)==OPEN_B);s.locked=false;
    s.present|=1u<<10;assert(hit(960+.5,false,s)==10&&marker_for((State){10,1024,1056,false})==10);
    s.present=2046;assert(hit(560,false,s)==WEATHER&&hit(640,false,s)==MARKETS&&hit(720,false,s)==PIANO&&hit(800,false,s)==FILES&&hit(880,false,s)==NEWS&&hit(960,false,s)==6&&hit(1250,false,s)==10&&hit(480,true,s)==FKEY+5&&hit(controls_start()-1,false,s)==10);
    assert(hit(control_x(POMODORO),false,s)==POMODORO&&hit(control_x(POMODORO)+79.99,true,s)==POMODORO);
    timer_menu=true;
    assert(hit(0,false,s)==TIMER_BACK&&hit(239,false,s)==0&&hit(880,false,s)==0);
    for(int i=0;i<4;i++){assert(hit(240+i*160,false,s)==TIMER_PRESET+i);assert(hit(399.99+i*160,false,s)==TIMER_PRESET+i);}
    assert(hit(1240,false,s)==0&&hit(1440,false,s)==0);
    pomodoro_deadline=1000;
    assert(hit(1240,false,s)==TIMER_PAUSE&&hit(1399.99,false,s)==TIMER_PAUSE&&hit(1400,false,s)==0);
    assert(hit(1440,false,s)==TIMER_CLEAR&&hit(1599.99,false,s)==TIMER_CLEAR&&hit(1600,false,s)==0);
    s.locked=true;assert(hit(1240,false,s)==0&&hit(1440,false,s)==0);pomodoro_deadline=0;
    s.locked=true;assert(hit(300,false,s)==0);s.locked=false;timer_menu=false;
    assert(control_x(THEME)+key_width==control_x(WIFI)&&control_x(WIFI)+key_width==control_x(BATTERY)&&control_x(BATTERY)+key_width==2170);
    for(int i=1;i<=3;i++){slider_mode=i;assert(hit(400,false,s)==MINUS&&hit(1000,false,s)==SLIDE&&hit(1600,false,s)==PLUS&&hit(1700,false,s)==(i==2?MUTE:DONE)&&hit(control_x(THEME)+50,false,s)==THEME);assert(slider_percent(100)==(i==1?1:0)&&slider_percent(1900)==100&&slider_percent(1035)==50);}slider_mode=0;
    for(size_t i=0;i<sizeof controls/sizeof controls[0];i++)assert(controls[i].width>=key_width);
    assert(controls[0].width==80&&controls[1].width==80&&controls[2].width==80&&controls[3].width==80&&controls[5].width==80);
    assert(slider_mode==0);slider_mode=2;assert(hit(1700,false,s)==MUTE&&hit(1800,false,s)==DONE&&hit(1900,false,s)==0);slider_mode=1;assert(hit(1800,false,s)==0);slider_mode=0;
    assert(speaker_hold_due(OPEN_V,true,false,false,.6));
    assert(!speaker_hold_due(OPEN_V,true,false,false,.59)&&!speaker_hold_due(OPEN_V,true,true,false,1)&&!speaker_hold_due(OPEN_V,true,false,true,1)&&!speaker_hold_due(OPEN_B,true,false,false,1)&&!speaker_hold_due(OPEN_V,false,false,false,1));
    int values[3],m;assert(parse_levels("LEVELS 20 40 60 0",values,&m)&&values[2]==60);assert(!parse_levels("LEVELS 20 101 60 0",values,&m));
    int night;assert(parse_nightlight("NIGHTLIGHT 1",&night)&&night==1);
    assert(parse_nightlight("NIGHTLIGHT 0",&night)&&night==0);
    assert(parse_nightlight("NIGHTLIGHT -1",&night)&&night==-1);
    assert(!parse_nightlight("NIGHTLIGHT 2",&night)&&!parse_nightlight("NIGHTLIGHT 0 junk",&night));
    assert(hit(control_x(NIGHTLIGHT),false,s)==NIGHTLIGHT&&hit(control_x(NIGHTLIGHT)+79.99,false,s)==NIGHTLIGHT);
    int mic,play;assert(parse_aux("AUX 1 0",&mic,&play)&&mic==1&&play==0);assert(parse_aux("AUX -1 -1",&mic,&play));
    assert(!parse_aux("AUX 0 2",&mic,&play)&&!parse_aux("AUX 0 1 junk",&mic,&play));
    assert(controls_start()==1290&&control_x(POMODORO)==control_x(TOGGLE)+80&&control_x(NIGHTLIGHT)==control_x(POMODORO)+80&&control_x(PLAYPAUSE)==control_x(NIGHTLIGHT)+80);
    assert(control_x(MIC_MUTE)+80==control_x(OPEN_V)&&control_x(OPEN_V)+80==control_x(THEME));
    assert(parse_state("STATE 5 32 32 0",&s));assert(!parse_state("STATE 11 0 0 0",&s));assert(!parse_state("STATE 1 0 0 0 garbage",&s));
    weather_mode=true;assert(hit(0,false,s)==WEATHER_BACK&&hit(200,false,s)==0&&hit(control_x(THEME),false,s)==THEME);weather_mode=false;
    assert(parse_weather("WEATHER 1 58125 1 0 -20751 0 29 19"));
    assert(weather_report.count==1&&weather_report.days[0][2]==29);
    assert(!parse_weather("WEATHER 1 58125 7 0 -20751 0 29 19"));
    assert(!parse_weather("WEATHER 1 58125 1 0 -20751 0 10 19"));
    assert(!parse_weather("WEATHER 1 58125 1 0 -20751 0 29 19 extra"));
    assert(parse_weather("WEATHER -1 -1 0 0"));
    unsigned char report[52]={0};report[0]=0x10;report[1]=0xff;report[2]=0x3f;
    bool down,multi;double x;assert(touch_packet(report,52,&down,&x,&multi)&&down&&!multi&&x>1080&&x<1090);
    report[4]=0x10;assert(touch_packet(report,52,&down,&x,&multi)&&multi);
    assert(!touch_packet(report,39,&down,&x,&multi));puts(sockets?"SELF_TEST_OK":"SELF_TEST_LOGIC_OK (socket round-trips skipped)");
}
int main(int argc,char **argv){
    setvbuf(stdout,NULL,_IOLBF,0);
    if(argc==2&&!strcmp(argv[1],"--self-test-features")){
        configure_widths();State test={.active=10,.present=2046};
        for(int mask=0;mask<64;mask++){
            for(int i=0;i<6;i++)optional_on[i]=(mask&(1<<i))!=0;
            double left=480;for(int i=0;i<6;i++)if(optional_on[i]){assert(hit(left+40,false,test)==optional_actions[i]);left+=80;}
            for(int i=0;i<5;i++)assert(hit(optional_end()+(i+.5)*workspace_step(),false,test)==i+6);
            test.locked=true;assert(hit(480,false,test)==0);test.locked=false;
        }puts("FEATURE_LAYOUT_TEST_OK");return 0;
    }
    if(!(argc==2&&(!strcmp(argv[1],"--self-test")||!strcmp(argv[1],"--self-test-logic"))))load_features();
    if(argc==3&&!strcmp(argv[1],"--check-news-packet"))return parse_news(argv[2])?0:1;
    if(argc==2&&(!strcmp(argv[1],"--self-test")||!strcmp(argv[1],"--self-test-logic"))){self_test(!strcmp(argv[1],"--self-test"));return 0;}
    if(argc==3&&(!strcmp(argv[1],"--render")||!strncmp(argv[1],"--render-pomodoro",17)||!strncmp(argv[1],"--render-piano",14)||!strncmp(argv[1],"--render-files",14)||!strncmp(argv[1],"--render-news",13)||!strcmp(argv[1],"--render-persistent")||!strcmp(argv[1],"--render-light")||!strcmp(argv[1],"--render-light-slider")||!strncmp(argv[1],"--render-weather",16)||!strncmp(argv[1],"--render-slider-",16))){
        if(!strncmp(argv[1],"--render-piano",14)){piano_mode=true;piano_mask=(1u<<0)|(1u<<4)|(1u<<7);piano_status=1;piano_sustain=strstr(argv[1],"sustain")!=NULL;if(strstr(argv[1],"low"))piano_octave=-2;if(strstr(argv[1],"high"))piano_octave=2;piano_demo_menu=strstr(argv[1],"menu")!=NULL;piano_demo_page=strstr(argv[1],"advanced")!=NULL;piano_demo_playing=strstr(argv[1],"playing")!=NULL;piano_demo_mask=piano_mask;}
        if(!strncmp(argv[1],"--render-files",14)){files_menu=strstr(argv[1],"menu")!=NULL;}
        if(!strncmp(argv[1],"--render-news",13)){
            news_mode=strstr(argv[1],"home")==NULL;news_menu=strstr(argv[1],"menu")!=NULL;news_started=now();
            news_report=(NewsReport){.category=0,.status=1,.position=1,.count=20,.published=(long long)time(NULL)-1800};
            strcpy(news_report.token,"0123456789abcdef");strcpy(news_report.title,"Example headline: a new generation of AI tools arrives");battery_percent=78;
            if(strstr(argv[1],"long"))strcpy(news_report.title,"Example long headline: researchers explore how a new generation of artificial intelligence tools could change the way people work, create and communicate across the world");
            if(strstr(argv[1],"cached"))news_report.status=2;
            if(strstr(argv[1],"offline")){news_report.status=-1;news_report.count=0;strcpy(news_report.title,"News unavailable — try again shortly");}
        }
        configure_widths();load_app_icon();light_mode=!strncmp(argv[1],"--render-light",14);
        if(strstr(argv[1],"news")&&strstr(argv[1],"light"))light_mode=true;
        if(strstr(argv[1],"piano-light")||strstr(argv[1],"files-light"))light_mode=true;
        persistent_mode=strcmp(argv[1],"--render")!=0;
        if(!strcmp(argv[1],"--render-light-slider"))slider_mode=2;
        if(!strncmp(argv[1],"--render-slider-",16)){const char *kind=strchr(media_kind,argv[1][16]);if(!kind||!*kind)return 2;slider_mode=(int)(kind-media_kind)+1;}
        if(!strncmp(argv[1],"--render-weather",16)){
            weather_mode=strstr(argv[1],"home")==NULL;light_mode=strstr(argv[1],"light")!=NULL;
            int codes[7]={0,2,3,61,95,71,45};weather_report=(WeatherReport){.status=1,.icon=0xe30d,.count=7};
            for(int i=0;i<7;i++){weather_report.days[i][0]=20751+i;weather_report.days[i][1]=codes[i];weather_report.days[i][2]=29-i;weather_report.days[i][3]=19-i;}weather_report.days[0][0]*=-1;
            if(strstr(argv[1],"offline"))weather_report=(WeatherReport){.status=-1,.icon=-1};
        }
        if(!strncmp(argv[1],"--render-pomodoro",17)){
            light_mode=strstr(argv[1],"light")!=NULL;
            pomodoro_deadline=timer_now()+(strstr(argv[1],"overtime")?-65:strstr(argv[1],"red")?299:1500);
            timer_menu=strstr(argv[1],"menu")!=NULL;
            if(strstr(argv[1],"paused"))timer_control(TIMER_PAUSE,timer_now());
            if(strstr(argv[1],"clear"))timer_control(TIMER_CLEAR,timer_now());
        }
        configure_widths();
        levels[0]=70;levels[1]=40;levels[2]=50;
        cairo_surface_t *surface=cairo_image_surface_create(CAIRO_FORMAT_RGB24,2170,60);
        paint(surface,2170,60,(State){5,32,32,false},false,4,0,false);
        int result=cairo_surface_write_to_png(surface,argv[2]);cairo_surface_destroy(surface);return result?1:0;
    }
    calibration_mode=argc==7&&!strcmp(argv[6],"--calibrate");
    persistent_mode=argc==7&&!strcmp(argv[6],"--persistent");
    bool keep_awake=persistent_mode||(argc==7&&!strcmp(argv[6],"--keep-awake"));
    if(argc!=6&&!calibration_mode&&!keep_awake){fprintf(stderr,"selector DRM HIDRAW SESSION_SOCKET UID DURATION [--calibrate|--keep-awake|--persistent]\n");return 2;}
    char *end;long user=strtol(argv[4],&end,10);if(*end||user<1000||user>INT_MAX)return 2;
    long duration=strtol(argv[5],&end,10);if(*end||(persistent_mode?duration!=0:(duration<30||duration>600)))return 2;
    signal(SIGTERM,stop);signal(SIGINT,stop);signal(SIGPIPE,SIG_IGN);
    int fd=open(argv[1],O_RDWR|O_CLOEXEC);if(fd<0){perror("DRM");return 1;}
    drmVersionPtr version=drmGetVersion(fd);
    if(!version||strcmp(version->name,"appletbdrm")){fprintf(stderr,"Wrong display driver\n");return 1;}drmFreeVersion(version);
    if(drmSetMaster(fd)){perror("DRM master");return 1;}
    int input=open(argv[2],O_RDONLY|O_NONBLOCK|O_CLOEXEC);struct hidraw_devinfo info={0};
    if(input<0||ioctl(input,HIDIOCGRAWINFO,&info)||info.vendor!=0x05ac||(uint16_t)info.product!=0x8600){fprintf(stderr,"Wrong digitizer\n");return 1;}
    int bridge=socket(AF_UNIX,SOCK_SEQPACKET|SOCK_CLOEXEC,0);struct sockaddr_un address={.sun_family=AF_UNIX};
    if(strlen(argv[3])>=sizeof address.sun_path)return 2;
    strcpy(address.sun_path,argv[3]);
    struct stat st;if(lstat(argv[3],&st)||!S_ISSOCK(st.st_mode)||st.st_uid!=(uid_t)user||(st.st_mode&077)!=0)return 1;
    if(connect(bridge,(struct sockaddr*)&address,sizeof address)){perror("session bridge");return 1;}
    struct ucred peer;socklen_t plen=sizeof peer;if(getsockopt(bridge,SOL_SOCKET,SO_PEERCRED,&peer,&plen)||peer.uid!=(uid_t)user)return 1;
    struct pollfd initial={.fd=bridge,.events=POLLIN};if(poll(&initial,1,3000)!=1)return 1;
    char packet[2048];ssize_t bytes=recv(bridge,packet,sizeof packet-1,0);if(bytes<=0)return 1;packet[bytes]=0;
    State state;if(!parse_state(packet,&state)||(state.locked&&!persistent_mode))return 1;
    fcntl(bridge,F_SETFL,fcntl(bridge,F_GETFL)|O_NONBLOCK);
    int keyboard=calibration_mode?-1:make_keyboard();if(keyboard<0&&!calibration_mode){perror("uinput");return 1;}
    struct passwd *pw=getpwuid((uid_t)user);if(!pw)return 1;
    if(setgroups(0,NULL)||setgid(pw->pw_gid)||setuid((uid_t)user)){perror("drop privileges");return 1;}
    setenv("HOME",pw->pw_dir,1);
    configure_widths();load_theme(pw->pw_dir);load_app_icon();battery_percent=read_battery("/sys/class/power_supply");
    printf("RENDERER_UID %u\n",(unsigned)getuid());
    printf("TOUCHBAR_THEME %s\n",light_mode?"light":"dark");
    drmModeRes *res=drmModeGetResources(fd);drmModeConnector *connector=NULL;if(!res)return 1;
    for(int i=0;i<res->count_connectors;i++){drmModeConnector *c=drmModeGetConnector(fd,res->connectors[i]);if(c&&c->connection==DRM_MODE_CONNECTED&&c->count_modes){connector=c;break;}if(c)drmModeFreeConnector(c);}
    if(!connector)return 1;
    drmModeModeInfo mode=connector->modes[0];int width=mode.hdisplay,height=mode.vdisplay;
    if(!((width==60&&height==2170)||(height==60&&width==2170)))return 1;
    uint32_t crtc=0;for(int i=0;i<connector->count_encoders&&!crtc;i++){drmModeEncoder *e=drmModeGetEncoder(fd,connector->encoders[i]);if(!e)continue;for(int j=0;j<res->count_crtcs;j++)if(e->possible_crtcs&(1u<<j)){crtc=res->crtcs[j];break;}drmModeFreeEncoder(e);}if(!crtc)return 1;
    struct drm_mode_create_dumb create={.width=width,.height=height,.bpp=32};if(drmIoctl(fd,DRM_IOCTL_MODE_CREATE_DUMB,&create))return 1;
    uint32_t fb=0,handles[4]={create.handle},pitches[4]={create.pitch},offsets[4]={0};
    if(drmModeAddFB2(fd,width,height,DRM_FORMAT_XRGB8888,handles,pitches,offsets,&fb,0))return 1;
    struct drm_mode_map_dumb map={.handle=create.handle};if(drmIoctl(fd,DRM_IOCTL_MODE_MAP_DUMB,&map))return 1;
    void *pixels=mmap(NULL,create.size,PROT_READ|PROT_WRITE,MAP_SHARED,fd,map.offset);if(pixels==MAP_FAILED)return 1;
    cairo_surface_t *surface=cairo_image_surface_create_for_data(pixels,CAIRO_FORMAT_RGB24,width,height,create.pitch);
    if(cairo_surface_status(surface))return 1;
    double marker=marker_for(state),from=marker,target=marker,anim_start=0;
    paint(surface,width,height,state,false,marker,0,false);
    if(drmModeSetCrtc(fd,crtc,fb,0,0,&connector->connector_id,1,&mode)){perror("modeset");return 1;}
    puts(persistent_mode?"SELECTOR_READY persistent compact uniform keys, right-aligned controls, ChatGPT SVG cutout, B/V/K sliders, light/dark, shared weather forecast, Omarchy control icons, microphone and playback, hourly weather navigation, nested mute, speaker hold, red warnings and battery, borderless mute icons, blue selection and charging, nightlight toggle, battery details menu, power profile cycle, wifi details menu, markets view, Pomodoro timer, playable piano with eight demo songs, piano octave and sustain, files shortcut and tap folder menu, RSS news ticker with swipe navigation":"SELECTOR_READY compact uniform keys, right-aligned controls, ChatGPT SVG cutout");
    printf("TRIAL_POLICY duration_seconds=%ld idle_dimming=%s refresh_seconds=%d\n",duration,keep_awake?"disabled":"30",keep_awake?10:0);
    if(calibration_mode)puts("CALIBRATION_READY actions_disabled=1");
    struct pollfd polls[]={{.fd=bridge,.events=POLLIN},{.fd=input,.events=POLLIN}};
    double deadline=persistent_mode?INFINITY:now()+duration,last_state=now(),last_frame=0,last_activity=now(),last_touch=now(),pressed_since=0;
    bool fn=false,dirty=false,dim=false,contact=false,cancelled=false;int pressed=0,held_key=0;unsigned actions=0,frames=1;
    int pending_value=-1;double last_media_send=0,last_battery=now();bool speaker_held=false,news_held=false;
    int result=0;
    while(!stopped&&now()<deadline){
        int ready=poll(polls,2,20);if(ready<0&&errno!=EINTR){result=1;break;}
        if(polls[0].revents&(POLLERR|POLLHUP|POLLNVAL)){result=persistent_mode?0:1;break;}
        if(polls[1].revents&(POLLERR|POLLHUP|POLLNVAL)){result=1;break;}
        if(polls[0].revents&POLLIN){
            while((bytes=recv(bridge,packet,sizeof packet-1,0))>0){
                packet[bytes]=0;
                if(!strncmp(packet,"NEWS ",5)){
                    if(!parse_news(packet)){result=1;stopped=1;break;}
                    dirty=true;continue;
                }
                if(!strncmp(packet,"PIANO SETTINGS ",15)){
                    int octave,sustain;char extra;
                    if(sscanf(packet,"PIANO SETTINGS %d %d %c",&octave,&sustain,&extra)!=2||octave< -2||octave>2||sustain<0||sustain>1){result=1;stopped=1;break;}
                    piano_octave=octave;piano_sustain=sustain;dirty=true;continue;
                }
                if(!strncmp(packet,"PIANO DEMO ",11)){
                    int index,playing;unsigned mask;char extra;
                    if(sscanf(packet,"PIANO DEMO %d %d %u %c",&index,&playing,&mask,&extra)!=3||index<0||index>7||playing<0||playing>1||mask>0x1ffffff){result=1;stopped=1;break;}
                    piano_demo_index=index;piano_demo_playing=playing;piano_demo_mask=mask;
                    if(playing)piano_mask=0;
                    dirty=true;continue;
                }
                if(!strncmp(packet,"PIANO STATUS ",13)){
                    int value;char extra;if(sscanf(packet,"PIANO STATUS %d %c",&value,&extra)!=1||value< -1||value>1){result=1;stopped=1;break;}
                    piano_status=value;if(value!=1){piano_demo_playing=false;piano_demo_mask=0;}dirty=true;continue;
                }
                if(!strncmp(packet,"MARKET ",7)){
                    if(!parse_market(packet)){result=1;stopped=1;break;}
                    dirty=true;continue;
                }
                if(!strncmp(packet,"WIFIINFO ",9)){
                    if(!parse_wifi_details(packet)){result=1;stopped=1;break;}
                    dirty=true;continue;
                }
                if(!strncmp(packet,"BATINFO ",8)){
                    if(!parse_battery_details(packet)){result=1;stopped=1;break;}
                    dirty=true;continue;
                }
                if(!strncmp(packet,"NIGHTLIGHT ",11)){
                    if(!parse_nightlight(packet,&nightlight_enabled)){result=1;stopped=1;break;}
                    dirty=true;continue;
                }
                if(!strncmp(packet,"AUX ",4)){
                    if(!parse_aux(packet,&mic_muted,&playing)){result=1;stopped=1;break;}
                    dirty=true;continue;
                }
                if(!strncmp(packet,"WEATHER ",8)){
                    if(!parse_weather(packet)){result=1;stopped=1;break;}
                    dirty=true;continue;
                }
                if(!strncmp(packet,"HOURS ",6)){
                    if(!parse_hourly(packet)){result=1;stopped=1;break;}
                    dirty=true;continue;
                }
                if(!strncmp(packet,"LEVELS ",7)){
                    int values[3],m;if(!parse_levels(packet,values,&m)){result=1;stopped=1;break;}
                    for(int i=0;i<3;i++)if(!(slider_mode==i+1&&((contact&&pressed==SLIDE)||pending_value>=0)))levels[i]=values[i];
                    muted=m;dirty=true;continue;
                }
                State next;if(!parse_state(packet,&next)){result=1;stopped=1;break;}
                last_state=now();if(next.locked&&!persistent_mode){stopped=1;break;}
                if(next.locked!=state.locked){if(held_key){emit_key(keyboard,held_key,0);held_key=0;}contact=false;cancelled=true;pressed=0;fn=false;slider_mode=0;weather_mode=false;battery_mode=false;wifi_mode=false;markets_mode=false;timer_menu=false;files_menu=false;news_mode=false;news_menu=false;news_frozen=false;news_pending_valid=false;send(bridge,"NEWS CLOSE",10,MSG_NOSIGNAL);piano_mode=false;piano_mask=0;piano_back_contact=false;piano_demo_menu=false;piano_demo_playing=false;piano_contact=false;piano_touch_action=0;send(bridge,"PIANO CLOSE",11,MSG_NOSIGNAL);pending_value=-1;printf("SESSION_LOCKED %d\n",next.locked);}
                if(next.active!=state.active||next.occupied!=state.occupied||next.present!=state.present||next.locked!=state.locked){
                    from=marker;state=next;target=marker_for(state);anim_start=now();dirty=true;last_activity=now();
                    printf("WORKSPACE_STATE active=%d occupied=%u present=%u\n",state.active,state.occupied,state.present);
                }
            }
        }
        if(polls[1].revents&POLLIN){
            unsigned char report[256];ssize_t size;
            while((size=read(input,report,sizeof report))>0){
                bool down,multi;double x;if(!touch_packet(report,(size_t)size,&down,&x,&multi))continue;
                if(piano_mode){
                    int contacts;bool back;unsigned notes=piano_report(report,&contacts,&back);
                    last_touch=now();last_activity=now();
                    int action=contacts==1?piano_control_hit(x):0;
                    if(contacts&&!piano_contact){piano_touch_action=action;piano_touch_since=now();piano_hold_handled=false;}
                    if(contacts&&action!=piano_touch_action)piano_touch_action=0;
                    if(!contacts&&piano_contact){
                        if(piano_hold_due(now())){
                            piano_hold_handled=true;piano_demo_menu=true;piano_demo_page=piano_demo_index/4;piano_demo_playing=false;piano_demo_mask=0;
                            if(!piano_send(bridge,0)||!piano_command(bridge,"PIANO DEMO STOP")){result=1;stopped=1;}
                        }
                        if(piano_touch_action==PIANO_BACK){
                            if(piano_demo_menu)piano_demo_menu=false;
                            else {piano_mode=false;piano_demo_playing=false;piano_demo_mask=0;piano_mask=0;
                                if(!piano_command(bridge,"PIANO CLOSE")){result=1;stopped=1;}}
                        }
                        if(piano_touch_action==PIANO_DEMO&&!piano_hold_handled){
                            if(!piano_send(bridge,0)||!piano_command(bridge,"PIANO DEMO TOGGLE")){result=1;stopped=1;}
                            piano_demo_playing=!piano_demo_playing;piano_demo_mask=0;
                        }
                        if(piano_touch_action==PIANO_DOWN||piano_touch_action==PIANO_UP||piano_touch_action==PIANO_SUSTAIN){
                            const char *request=piano_touch_action==PIANO_DOWN?"PIANO OCTAVE -1":piano_touch_action==PIANO_UP?"PIANO OCTAVE 1":"PIANO SUSTAIN TOGGLE";
                            if(!piano_send(bridge,0)||!piano_command(bridge,request)){result=1;stopped=1;}
                        }
                        if(piano_touch_action==PIANO_PAGE)piano_demo_page=1-piano_demo_page;
                        if(piano_touch_action>=PIANO_SONG&&piano_touch_action<PIANO_SONG+8){
                            char request[48];snprintf(request,sizeof request,"PIANO DEMO SELECT %d",piano_touch_action-PIANO_SONG);
                            if(!piano_command(bridge,request)){result=1;stopped=1;}
                            piano_demo_menu=false;
                        }
                        piano_touch_action=0;
                    }
                    piano_contact=contacts>0;piano_back_contact=contacts==1&&action==PIANO_BACK;
                    if(state.locked||piano_demo_playing||piano_demo_menu||!piano_mode)notes=0;
                    if(!piano_demo_playing&&(notes!=piano_mask||now()-piano_last_send>.25)){
                        if(!piano_send(bridge,notes)){result=1;stopped=1;}}
                    dirty=true;continue;
                }
                last_touch=now();last_activity=now();int action=down?hit(x,fn,state):0;
                if(calibration_mode){
                    if(down&&!contact&&!multi)printf("CALIBRATION_TOUCH raw_normalized=%.5f logical_x=%.1f region=%d\n",x/2170,x,action);
                    calibration_x=down?x:-1;contact=down;dirty=true;continue;
                }
                if(multi){cancelled=true;if(held_key){emit_key(keyboard,held_key,0);held_key=0;}}
                if(down&&!contact&&!multi){
                    contact=true;cancelled=false;pressed=action;held_key=key_for(action);pressed_since=now();speaker_held=false;news_held=false;
                    if(news_mode&&!battery_mode){news_frozen=true;news_frozen_at=now();}
                    if(pressed==NEWS_HEADLINE)news_touch_begin(x);
                    if(held_key&&!emit_key(keyboard,held_key,1)){result=1;stopped=1;}
                    if(held_key)printf("KEY_DOWN code=%d\n",held_key);
                    dirty=true;
                }else if(down&&contact&&action!=pressed&&pressed!=SLIDE){cancelled=true;if(held_key){emit_key(keyboard,held_key,0);held_key=0;}}
                if(down&&contact&&pressed==NEWS_HEADLINE)news_touch_move(x);
                if(down&&contact&&!cancelled&&pressed==SLIDE&&slider_mode&&levels[slider_mode-1]>=0){
                    int value=slider_percent(x);
                    if(value!=levels[slider_mode-1]){levels[slider_mode-1]=value;pending_value=value;dirty=true;}
                }
                if(!down&&contact){
                    if(held_key){emit_key(keyboard,held_key,0);printf("KEY_UP code=%d\n",held_key);held_key=0;}
                    if(news_hold_due(pressed,contact,cancelled,news_held,now()-pressed_since)){news_menu=true;news_held=true;}
                    /* Also classify at release if it arrived on the threshold tick. */
                    if(speaker_hold_due(pressed,contact,cancelled,speaker_held,now()-pressed_since)){
                        if(!speaker_mute_request(bridge)){result=1;stopped=1;}
                        speaker_held=true;puts("SPEAKER_HOLD_MUTE");
                    }
                    if(!cancelled){
                        int swipe=pressed==NEWS_HEADLINE?news_swipe(cancelled,news_held):0;
                        if(swipe){
                            if(!news_command(bridge,swipe>0?"NEWS NEXT":"NEWS PREV")){result=1;stopped=1;}
                            news_next_wait=true;news_next_sent=now();news_pending_valid=false;
                        }
                        if(!news_action(bridge,pressed,news_held||(pressed==NEWS_HEADLINE&&news_touch_moved()))){result=1;stopped=1;}
                        if(files_navigate(pressed))puts("FILES_MENU_CHANGED");
                        if(pressed>=FILES_FOLDER&&pressed<FILES_FOLDER+7){
                            if(!files_open(bridge,pressed-FILES_FOLDER)){result=1;stopped=1;}
                            files_menu=false;
                        }
                        if(pressed>=1&&pressed<=10){char request[16];int n=snprintf(request,sizeof request,"WS %d",pressed);if(send(bridge,request,n,MSG_NOSIGNAL)!=n){result=1;stopped=1;}else{printf("WORKSPACE_TAP %d\n",pressed);actions++;}}
                        if(pressed==MARKETS||pressed==MARKETS_BACK){
                            markets_mode=pressed==MARKETS?!markets_mode:false;
                            const char *request=markets_mode?"MARKETS OPEN":"MARKETS CLOSE";
                            if(send(bridge,request,strlen(request),MSG_NOSIGNAL)!=(ssize_t)strlen(request)){result=1;stopped=1;}
                            printf("MARKETS_VIEW open=%d\n",markets_mode);
                        }
                        if(markets_mode&&(pressed==BATTERY||pressed==WIFI)){
                            markets_mode=false;const char *request="MARKETS CLOSE";send(bridge,request,strlen(request),MSG_NOSIGNAL);
                        }
                        if(pressed==WIFI||pressed==WIFI_BACK){
                            wifi_mode=pressed==WIFI?!wifi_mode:false;
                            const char *request=wifi_mode?"WIFI OPEN":"WIFI CLOSE";
                            if(send(bridge,request,strlen(request),MSG_NOSIGNAL)!=(ssize_t)strlen(request)){result=1;stopped=1;}
                            printf("WIFI_VIEW open=%d\n",wifi_mode);
                        }
                        if(pressed==BATTERY&&wifi_mode){const char *request="WIFI CLOSE";send(bridge,request,strlen(request),MSG_NOSIGNAL);}
                        if(battery_navigate(pressed)){
                            if(battery_mode){const char *request="BATTERY REFRESH";send(bridge,request,strlen(request),MSG_NOSIGNAL);}
                            printf("BATTERY_VIEW open=%d\n",battery_mode);
                        }
                        if(pressed==POWER_PROFILE){
                            const char *request="POWER PROFILE NEXT";
                            if(send(bridge,request,strlen(request),MSG_NOSIGNAL)!=(ssize_t)strlen(request)){result=1;stopped=1;}
                            else puts("POWER_PROFILE_TAP");
                        }
                        if(pressed==PIANO){piano_mode=true;piano_mask=0;piano_back_contact=false;piano_status=0;piano_demo_menu=false;piano_contact=false;piano_touch_action=0;
                            if(send(bridge,"PIANO OPEN",10,MSG_NOSIGNAL)!=10){result=1;stopped=1;}puts("PIANO_OPEN");}
                        if(pressed==POMODORO){timer_menu=true;puts("TIMER_MENU_OPEN");}
                        if(pressed==TIMER_BACK)timer_menu=false;
                        if(timer_select(pressed,timer_now()))puts("TIMER_STARTED");
                        if(timer_control(pressed,timer_now()))puts("TIMER_CONTROL");
                        if(pressed==TOGGLE){fn=!fn;puts(fn?"FUNCTION_LAYER on":"FUNCTION_LAYER off");}
                        if(pressed>=OPEN_B&&pressed<=OPEN_K&&!speaker_held){slider_mode=pressed-OPEN_B+1;pending_value=-1;printf("SLIDER_OPEN %c\n",media_kind[slider_mode-1]);}
                        if(pressed==DONE){slider_mode=0;pending_value=-1;puts("SLIDER_CLOSED");}
                        if(weather_navigate(pressed))printf("WEATHER_VIEW open=%d date=%d page=%d\n",weather_mode,weather_date,weather_hour_page);
                        if(pressed==WEATHER){const char *request="WEATHER REFRESH";send(bridge,request,strlen(request),MSG_NOSIGNAL);puts("WEATHER_OPEN");}
                        if(pressed==CHATGPT){const char *request="APP CHATGPT";if(send(bridge,request,strlen(request),MSG_NOSIGNAL)!=(ssize_t)strlen(request)){result=1;stopped=1;}else{app_feedback_until=now()+.6;puts("APP_TAP CHATGPT");}}
                        if(pressed==NIGHTLIGHT||pressed==MIC_MUTE||pressed==PLAYPAUSE||pressed==MUTE){
                            const char *request=pressed==NIGHTLIGHT?"BUTTON NIGHTLIGHT":pressed==MIC_MUTE?"BUTTON MIC_MUTE":pressed==MUTE?"BUTTON SPEAKER_MUTE":"BUTTON PLAYPAUSE";
                            if(send(bridge,request,strlen(request),MSG_NOSIGNAL)!=(ssize_t)strlen(request)){result=1;stopped=1;}else printf("%s\n",request);
                        }
                        if(pressed==THEME){light_mode=!light_mode;bool saved=save_theme();printf("TOUCHBAR_THEME %s saved=%d\n",light_mode?"light":"dark",saved);if(!saved)perror("theme preference");}
                        if((pressed==MINUS||pressed==PLUS)&&slider_mode&&levels[slider_mode-1]>=0){
                            int direction=pressed==PLUS?1:-1;
                            if(media_request(bridge,"STEP",direction)<0){result=1;stopped=1;}
                            levels[slider_mode-1]=(int)fmax(slider_mode==1?1:0,fmin(100,levels[slider_mode-1]+direction));
                        }
                        if(pressed==BACK&&!persistent_mode){
                            if(now()-pressed_since>=1){puts("EXIT_HOLD_CONFIRMED");stopped=1;}
                            else puts("EXIT_SHORT_TAP_IGNORED");
                        }
                    }
                    if(news_frozen){news_frozen=false;news_started=now();
                        if(news_pending_valid){news_report=news_pending;news_pending_valid=false;news_next_wait=false;}}
                    contact=false;pressed=0;dirty=true;
                }
            }
        }
        if(news_hold_due(pressed,contact,cancelled,news_held,now()-pressed_since)&&now()-last_touch<=2){
            news_menu=true;news_held=true;dirty=true;
        }
        if(news_mode&&!news_menu&&!battery_mode&&!contact){
            if(now()-last_frame>=((news_overflow>0&&news_elapsed()>=2&&news_elapsed()<2+news_overflow/50)?.04:1))dirty=true;
            if(news_report.count&&news_elapsed()>=news_dwell()&&(!news_next_wait||now()-news_next_sent>2)){
                if(!news_command(bridge,"NEWS NEXT")){result=1;break;}
                news_next_wait=true;news_next_sent=now();news_started=now();
            }
        }
        if(speaker_hold_due(pressed,contact,cancelled,speaker_held,now()-pressed_since)&&now()-last_touch<=2){
            if(!speaker_mute_request(bridge)){result=1;break;}
            speaker_held=true;dirty=true;puts("SPEAKER_HOLD_MUTE");
        }
        if(piano_mode&&piano_hold_due(now())&&now()-last_touch<=2){
            piano_hold_handled=true;piano_demo_menu=true;piano_demo_page=piano_demo_index/4;piano_demo_playing=false;piano_demo_mask=0;
            if(!piano_send(bridge,0)||!piano_command(bridge,"PIANO DEMO STOP")){result=1;break;}
            dirty=true;
        }
        if(piano_mode&&piano_contact&&now()-last_touch>2){piano_contact=false;piano_touch_action=0;piano_back_contact=false;dirty=true;}
        if(piano_mode&&piano_mask&&!piano_demo_playing){
            if(now()-last_touch>1){if(!piano_send(bridge,0)){result=1;break;}dirty=true;}
            else if(now()-piano_last_send>.25&&!piano_send(bridge,piano_mask)){result=1;break;}
        }
        static int last_pomodoro=-1;int remaining=pomodoro_deadline>0?pomodoro_remaining(timer_now()):0;
        if(remaining!=last_pomodoro){last_pomodoro=remaining;dirty=true;}
        if(now()-last_battery>=5){bool was_charging=battery_charging;int value=read_battery("/sys/class/power_supply");if(value!=battery_percent||was_charging!=battery_charging){battery_percent=value;dirty=true;}last_battery=now();}
        if(pending_value>=0&&slider_mode&&(now()-last_media_send>=.07||!contact)){
            int sent=media_request(bridge,"SET",pending_value);
            if(sent<0){result=1;break;}
            if(sent>0){pending_value=-1;last_media_send=now();}
        }
        if(now()-last_state>5){puts("SESSION_BRIDGE_TIMEOUT");result=1;break;}
        if(contact&&now()-last_touch>2){if(held_key){emit_key(keyboard,held_key,0);held_key=0;}contact=false;cancelled=true;pressed=0;news_frozen=false;news_pending_valid=false;news_started=now();dirty=true;}
        bool next_dim=!keep_awake&&now()-last_activity>30;if(next_dim!=dim){dim=next_dim;dirty=true;printf("IDLE_DIM %s\n",dim?"on":"off");}
        if(keep_awake&&now()-last_frame>=10)dirty=true;
        if(app_feedback_until>0){dirty=true;if(now()>=app_feedback_until)app_feedback_until=0;}
        if(marker!=target){
            double t=fmin(1,(now()-anim_start)/.16);marker=from+(target-from)*(1-pow(1-t,3));if(t>=1)marker=target;dirty=true;
        }
        if(dirty&&now()-last_frame>=.04){
            paint(surface,width,height,state,fn,marker,pressed,dim);
            if(drmModeDirtyFB(fd,fb,NULL,0)){perror("frame update");result=1;break;}
            frames++;last_frame=now();dirty=false;
        }
    }
    if(piano_mode)send(bridge,"PIANO CLOSE",11,MSG_NOSIGNAL);
    if(now()>=deadline)puts("TRIAL_RUNTIME_LIMIT");
    if(held_key)emit_key(keyboard,held_key,0);
    if(keyboard>=0){ioctl(keyboard,UI_DEV_DESTROY);close(keyboard);}
    printf("SELECTOR_FINISHED workspace_actions=%u frames=%u result=%d\n",actions,frames,result);
    drmModeSetCrtc(fd,crtc,0,0,0,NULL,0,NULL);cairo_surface_destroy(surface);munmap(pixels,create.size);drmModeRmFB(fd,fb);
    struct drm_mode_destroy_dumb destroy={.handle=create.handle};drmIoctl(fd,DRM_IOCTL_MODE_DESTROY_DUMB,&destroy);
    drmModeFreeConnector(connector);drmModeFreeResources(res);drmDropMaster(fd);close(fd);close(input);close(bridge);
    return result;
}
