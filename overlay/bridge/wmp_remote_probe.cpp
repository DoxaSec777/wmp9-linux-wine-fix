/*
 * Native WMP9 remote-control bridge used by the Linux overlay services.
 *
 * The process hosts WMPlayer.OCX.7 in a tiny hidden OLE site and advertises
 * IWMPRemoteMediaServices so the control attaches to the already-running WMP
 * GUI engine instead of creating an isolated player.  Normal sampling reads
 * automation properties only.  Startup-gate mode may pause, seek, and resume
 * the exact proxy URL supplied by the launcher; shutdown likewise stops only
 * that owned URL.
 *
 * COM/OLE lifetime is deliberately contained in this process.  Nested
 * IDispatch interfaces are reacquired for every sample, all references are
 * released before IOleObject::Close and SetClientSite(nullptr), and the OLE
 * apartment is uninitialized last.  The Python owner requests this graceful
 * path with a per-run stop marker before using terminate/kill as a fallback.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <ole2.h>
#include <ocidl.h>
#include <servprov.h>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

// SDK ABI: IWMPRemoteMediaServices (WMP9+), declared locally to avoid ATL.
static const IID RemoteIID={0xcbb92747,0x741f,0x44fe,{0xab,0x5b,0xf1,0xa4,0x8f,0x3b,0x2a,0x59}};
struct RemoteServices : IUnknown {
 virtual HRESULT STDMETHODCALLTYPE GetServiceType(BSTR*)=0;
 virtual HRESULT STDMETHODCALLTYPE GetApplicationName(BSTR*)=0;
 virtual HRESULT STDMETHODCALLTYPE GetScriptableObject(BSTR*,IDispatch**)=0;
 virtual HRESULT STDMETHODCALLTYPE GetCustomUIMode(BSTR*)=0;
};
static void status(const char* step,HRESULT hr) {fprintf(stderr,"%s: 0x%08lx\n",step,(unsigned long)hr);fflush(stderr);}
// One reference-counted object supplies every interface requested by the
// ActiveX control.  Interlocked reference counts are required because COM is
// free to AddRef/Release through any of these interface identities.
class Site final : public IOleClientSite,public IServiceProvider,public RemoteServices,public IOleInPlaceSite {
 LONG refs=1;
 public:
 HWND hwnd;
 explicit Site(HWND h):hwnd(h){}
 HRESULT STDMETHODCALLTYPE QueryInterface(REFIID i,void**p) override {
  if(!p)return E_POINTER;
  *p=nullptr;
  if(i==IID_IUnknown||i==IID_IOleClientSite)*p=static_cast<IOleClientSite*>(this);
  else if(i==IID_IServiceProvider)*p=static_cast<IServiceProvider*>(this);
  else if(i==RemoteIID)*p=static_cast<RemoteServices*>(this);
  else if(i==IID_IOleWindow||i==IID_IOleInPlaceSite)*p=static_cast<IOleInPlaceSite*>(this);
  else return E_NOINTERFACE;
  AddRef();return S_OK;
 }
 ULONG STDMETHODCALLTYPE AddRef() override{return InterlockedIncrement(&refs);}
 ULONG STDMETHODCALLTYPE Release() override{ULONG n=InterlockedDecrement(&refs);if(!n)delete this;return n;}
 HRESULT STDMETHODCALLTYPE QueryService(REFGUID service,REFIID riid,void**p) override {
  wchar_t sg[40]={},ig[40]={};StringFromGUID2(service,sg,40);StringFromGUID2(riid,ig,40);
  fwprintf(stderr,L"QueryService service=%ls iid=%ls\n",sg,ig);fflush(stderr);
  // WMP uses a distinct service GUID; the requested interface IID is the
  // authoritative test.  Comparing service to RemoteIID creates a local engine.
  if(riid==RemoteIID)return QueryInterface(riid,p);
  *p=nullptr;return E_NOINTERFACE;
 }
 HRESULT STDMETHODCALLTYPE GetServiceType(BSTR*p) override {status("GetServiceType Remote",S_OK);*p=SysAllocString(L"Remote");return *p?S_OK:E_OUTOFMEMORY;}
 HRESULT STDMETHODCALLTYPE GetApplicationName(BSTR*p) override {*p=SysAllocString(L"HAL WMP9 read-only probe");return *p?S_OK:E_OUTOFMEMORY;}
 HRESULT STDMETHODCALLTYPE GetScriptableObject(BSTR*n,IDispatch**p) override {*n=nullptr;*p=nullptr;return E_NOTIMPL;}
 HRESULT STDMETHODCALLTYPE GetCustomUIMode(BSTR*p) override {*p=nullptr;return E_NOTIMPL;}
 HRESULT STDMETHODCALLTYPE SaveObject() override{return E_NOTIMPL;}
 HRESULT STDMETHODCALLTYPE GetMoniker(DWORD,DWORD,IMoniker**p) override{*p=nullptr;return E_NOTIMPL;}
 HRESULT STDMETHODCALLTYPE GetContainer(IOleContainer**p) override{*p=nullptr;return E_NOINTERFACE;}
 HRESULT STDMETHODCALLTYPE ShowObject() override{return S_OK;}
 HRESULT STDMETHODCALLTYPE OnShowWindow(BOOL) override{return S_OK;}
 HRESULT STDMETHODCALLTYPE RequestNewObjectLayout() override{return E_NOTIMPL;}
 HRESULT STDMETHODCALLTYPE GetWindow(HWND*p) override{*p=hwnd;return S_OK;}
 HRESULT STDMETHODCALLTYPE ContextSensitiveHelp(BOOL) override{return E_NOTIMPL;}
 HRESULT STDMETHODCALLTYPE CanInPlaceActivate() override{return S_OK;}
 HRESULT STDMETHODCALLTYPE OnInPlaceActivate() override{return S_OK;}
 HRESULT STDMETHODCALLTYPE OnUIActivate() override{return S_OK;}
 HRESULT STDMETHODCALLTYPE GetWindowContext(IOleInPlaceFrame**f,IOleInPlaceUIWindow**d,RECT*p,RECT*c,OLEINPLACEFRAMEINFO*i) override {
  *f=nullptr;*d=nullptr;GetClientRect(hwnd,p);*c=*p;i->cb=sizeof(*i);i->fMDIApp=FALSE;i->hwndFrame=hwnd;i->haccel=nullptr;i->cAccelEntries=0;return S_OK;
 }
 HRESULT STDMETHODCALLTYPE Scroll(SIZE) override{return E_NOTIMPL;}
 HRESULT STDMETHODCALLTYPE OnUIDeactivate(BOOL) override{return S_OK;}
 HRESULT STDMETHODCALLTYPE OnInPlaceDeactivate() override{return S_OK;}
 HRESULT STDMETHODCALLTYPE DiscardUndoState() override{return E_NOTIMPL;}
 HRESULT STDMETHODCALLTYPE DeactivateAndUndo() override{return E_NOTIMPL;}
 HRESULT STDMETHODCALLTYPE OnPosRectChange(LPCRECT) override{return S_OK;}
};
static std::string utf8(const wchar_t*s,int len=-1){if(!s)return "";if(len<0)len=lstrlenW(s);int n=WideCharToMultiByte(CP_UTF8,0,s,len,nullptr,0,nullptr,nullptr);std::string out(n,'\0');if(n)WideCharToMultiByte(CP_UTF8,0,s,len,&out[0],n,nullptr,nullptr);return out;}
static std::string quote(const std::string&s){std::string out="\"";for(unsigned char c:s){if(c=='"'||c=='\\'){out+='\\';out+=c;}else if(c<32){char t[8];sprintf(t,"\\u%04x",c);out+=t;}else out+=c;}return out+'"';}
// Automation helpers always initialize/clear VARIANT storage.  child() takes an
// explicit reference before VariantClear releases the temporary dispatch value.
static HRESULT get(IDispatch*d,const wchar_t*name,VARIANT*v){VariantInit(v);if(!d)return E_POINTER;DISPID id;LPOLESTR n=const_cast<LPOLESTR>(name);HRESULT h=d->GetIDsOfNames(IID_NULL,&n,1,LOCALE_USER_DEFAULT,&id);if(FAILED(h))return h;DISPPARAMS a={};return d->Invoke(id,IID_NULL,LOCALE_USER_DEFAULT,DISPATCH_PROPERTYGET,&a,v,nullptr,nullptr);}
static IDispatch* child(IDispatch*d,const wchar_t*n){VARIANT v;HRESULT h=get(d,n,&v);IDispatch*p=nullptr;if(SUCCEEDED(h)&&v.vt==VT_DISPATCH&&v.pdispVal){p=v.pdispVal;p->AddRef();}VariantClear(&v);return p;}
static std::string val(IDispatch*d,const wchar_t*n,HRESULT*hr=nullptr){VARIANT v;HRESULT h=get(d,n,&v);if(hr)*hr=h;std::string s="null";char b[64];if(SUCCEEDED(h)){switch(v.vt){case VT_BOOL:s=v.boolVal?"true":"false";break;case VT_I4:s=std::to_string(v.lVal);break;case VT_R8:sprintf(b,"%.6f",v.dblVal);s=b;break;case VT_BSTR:s=quote(utf8(v.bstrVal,SysStringLen(v.bstrVal)));break;default:break;}}VariantClear(&v);return s;}
// The STA must pump messages while polling; sleeping without dispatching can
// deadlock ActiveX callbacks and make the remote GUI appear disconnected.
static void pump(DWORD ms){DWORD start=GetTickCount();do{MSG m;while(PeekMessageW(&m,nullptr,0,0,PM_REMOVE)){TranslateMessage(&m);DispatchMessageW(&m);}Sleep(10);}while(GetTickCount()-start<ms);}
static HRESULT transport(IDispatch*d,const wchar_t*name,bool put=false,double number=0){if(!d)return E_POINTER;DISPID id;LPOLESTR n=const_cast<LPOLESTR>(name);HRESULT h=d->GetIDsOfNames(IID_NULL,&n,1,LOCALE_USER_DEFAULT,&id);if(FAILED(h))return h;VARIANT v;VariantInit(&v);v.vt=VT_R8;v.dblVal=number;DISPID named=DISPID_PROPERTYPUT;DISPPARAMS a={};if(put){a.rgvarg=&v;a.cArgs=1;a.rgdispidNamedArgs=&named;a.cNamedArgs=1;}return d->Invoke(id,IID_NULL,LOCALE_USER_DEFAULT,put?DISPATCH_PROPERTYPUT:DISPATCH_METHOD,&a,nullptr,nullptr,nullptr);}
struct Win {HWND h;};
static BOOL CALLBACK children(HWND h,LPARAM p){reinterpret_cast<std::vector<Win>*>(p)->push_back({h});return TRUE;}
static DWORD playerPid=0;
// Skin mode can create a separate top-level host.  First discover the PID from
// WMPlayerApp, then enumerate every top-level HWND owned by that PID and all of
// their descendants rather than assuming one stable window hierarchy.
static BOOL CALLBACK findPlayer(HWND h,LPARAM){wchar_t cls[256]={};GetClassNameW(h,cls,256);if(lstrcmpW(cls,L"WMPlayerApp")==0)GetWindowThreadProcessId(h,&playerPid);return TRUE;}
static BOOL CALLBACK tops(HWND h,LPARAM p){DWORD pid=0;GetWindowThreadProcessId(h,&pid);if(playerPid && pid==playerPid){auto*w=reinterpret_cast<std::vector<Win>*>(p);w->push_back({h});EnumChildWindows(h,children,p);}return TRUE;}
static std::string windows(){playerPid=0;EnumWindows(findPlayer,0);std::vector<Win> ws;EnumWindows(tops,(LPARAM)&ws);std::string s="[";for(auto w:ws){if(s.size()>1)s+=",";wchar_t cls[256]={},title[512]={};GetClassNameW(w.h,cls,256);GetWindowTextW(w.h,title,512);RECT r={},c={};GetWindowRect(w.h,&r);GetClientRect(w.h,&c);POINT origin={0,0};ClientToScreen(w.h,&origin);DWORD pid=0;GetWindowThreadProcessId(w.h,&pid);char b[600];sprintf(b,"{\"hwnd\":%lu,\"parent\":%lu,\"pid\":%lu,\"visible\":%s,\"rect\":[%ld,%ld,%ld,%ld],\"clientScreen\":[%ld,%ld,%ld,%ld],\"class\":",(unsigned long)(ULONG_PTR)w.h,(unsigned long)(ULONG_PTR)GetParent(w.h),(unsigned long)pid,IsWindowVisible(w.h)?"true":"false",r.left,r.top,r.right-r.left,r.bottom-r.top,origin.x,origin.y,c.right,c.bottom);s+=b;s+=quote(utf8(cls));s+=",\"title\":"+quote(utf8(title))+"}";}return s+"]";}
int main(int argc,char**argv){
 int samples=argc>1?atoi(argv[1]):20;int interval=argc>2?atoi(argv[2]):250;
 if(samples<0||interval<10)return 64;
 bool enumOnly=argc>3&&std::string(argv[3])=="--enum-only";
 // Gate state is local to this proxy session.  WMP audio remains routed to the
 // launcher's private null sink until the Python service creates argv[4].
 bool startupGate=argc>5&&std::string(argv[3])=="--startup-gate";bool held=false,released=false;
 if(enumOnly){printf("{\"type\":\"windows\",\"windows\":%s}\n",windows().c_str());return 0;}
 HRESULT hr=OleInitialize(nullptr);status("OleInitialize",hr);if(FAILED(hr))return 1;
 HWND host=CreateWindowExW(0,L"STATIC",L"WMP9 Remote Probe hidden host",WS_POPUP,0,0,1,1,nullptr,nullptr,GetModuleHandleW(nullptr),nullptr);
 Site*site=new Site(host);CLSID cls;CLSIDFromProgID(L"WMPlayer.OCX.7",&cls);IOleObject*ole=nullptr;
 hr=CoCreateInstance(cls,nullptr,CLSCTX_INPROC_SERVER,IID_IOleObject,(void**)&ole);status("CoCreate IOleObject",hr);if(FAILED(hr))return 2;
 hr=ole->SetClientSite(site);status("SetClientSite",hr);if(FAILED(hr))return 3;
 IPersistStreamInit*psi=nullptr;if(SUCCEEDED(ole->QueryInterface(IID_IPersistStreamInit,(void**)&psi))){status("InitNew",psi->InitNew());psi->Release();}
 status("OleRun",OleRun(ole));
 if(argc>3&&std::string(argv[3])=="--activate"){RECT rect={0,0,1,1};status("DoVerb INPLACEACTIVATE",ole->DoVerb(OLEIVERB_INPLACEACTIVATE,nullptr,site,0,host,&rect));}
 IDispatch*player=nullptr;hr=ole->QueryInterface(IID_IDispatch,(void**)&player);status("QI IDispatch",hr);if(FAILED(hr))return 4;
 pump(500);bool remote=false;
 const char* stopFile=getenv("WMP9_PROBE_STOP");
 for(unsigned long long i=0;samples==0||i<(unsigned)samples;i++){
  if(stopFile && GetFileAttributesA(stopFile)!=INVALID_FILE_ATTRIBUTES){
   // Closing a remoted GUI only docks its engine; explicitly stop our own
   // proxy before COM release, never a different file selected by the user.
   IDispatch* media=child(player,L"currentMedia");
   if(startupGate && val(player,L"isRemote")=="true" && val(media,L"sourceURL")==quote(argv[5])){
    IDispatch* ctrl=child(player,L"controls");status("Owned proxy stop",transport(ctrl,L"stop"));if(ctrl)ctrl->Release();
   }
   if(media)media->Release();
   break;
  }
  // Reacquire nested interfaces every sample: skin/media reconnects can
  // invalidate cached controls/currentMedia pointers even while player survives.
  HRESULT rh;std::string rem=val(player,L"isRemote",&rh);remote=rem=="true";
  IDispatch*ctrl=child(player,L"controls");IDispatch*media=child(player,L"currentMedia");
  std::string state=val(player,L"playState"),pos=val(ctrl,L"currentPosition"),src=val(media,L"sourceURL");
  if(startupGate && remote && !released && src==quote(argv[5])){
   if(!held && state=="3") {HRESULT pause=transport(ctrl,L"pause");HRESULT seek=transport(ctrl,L"currentPosition",true,0);held=SUCCEEDED(pause)&&SUCCEEDED(seek);}
   if(held && GetFileAttributesA(argv[4])!=INVALID_FILE_ATTRIBUTES){status("Startup play",transport(ctrl,L"play"));released=true;}
   state=val(player,L"playState");pos=val(ctrl,L"currentPosition");
  }
  printf("{\"type\":\"sample\",\"tick\":%lu,\"isRemote\":%s,\"remoteHRESULT\":%ld,\"playState\":%s,\"currentPosition\":%s,\"sourceURL\":%s,\"startupHeld\":%s,\"windows\":%s}\n",(unsigned long)GetTickCount(),rem.c_str(),(long)rh,state.c_str(),pos.c_str(),src.c_str(),held&&!released?"true":"false",windows().c_str());fflush(stdout);
  if(ctrl)ctrl->Release();
  if(media)media->Release();
  pump(interval);
 }
 // Preserve teardown order.  In particular, detach the client site before its
 // final Release and do not uninitialize OLE while the control still owns refs.
 player->Release();ole->Close(OLECLOSE_NOSAVE);ole->SetClientSite(nullptr);ole->Release();site->Release();DestroyWindow(host);OleUninitialize();status("COM_RELEASED",S_OK);return remote?0:5;
}
