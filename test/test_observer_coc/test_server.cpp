#include <ble.h>
#ifdef COC_QUEUE_INCLUDE
#include COC_QUEUE_INCLUDE
#else
#include "observer/ObserverRecordQueue.h"
#endif
#include "observer/coc/Server.h"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <vector>

using namespace observer::coc;
using Queue = observer::RecordQueue<2048, 16>;

struct FakePort : Port {
  struct Tx { uint16_t handle, cid; const uint8_t* pointer; std::vector<uint8_t> bytes; };
  struct Rx { uint16_t cid; uint8_t* pointer; };
  uint16_t status = BLE_L2CAP_CH_STATUS_CODE_SUCCESS;
  uint32_t tx_status = NRF_SUCCESS;
  std::vector<Tx> sent;
  std::vector<Rx> receives;
  std::vector<uint16_t> refused, released;
  unsigned accepts = 0;
  uint32_t authorizes(uint16_t) override { return status; }
  uint32_t accept(uint16_t, uint16_t& cid, uint8_t* buffer, uint16_t) override {
    ++accepts; receives.push_back({cid, buffer}); return NRF_SUCCESS;
  }
  void refuse(uint16_t, uint16_t, uint16_t reason) override { refused.push_back(reason); }
  uint32_t receive(uint16_t, uint16_t cid, uint8_t* buffer, uint16_t) override {
    receives.push_back({cid, buffer}); return NRF_SUCCESS;
  }
  uint32_t send(uint16_t handle, uint16_t cid, const uint8_t* buffer, uint16_t n) override {
    if (tx_status) return tx_status;
    sent.push_back({handle, cid, buffer, std::vector<uint8_t>(buffer, buffer + n)});
    return NRF_SUCCESS;
  }
  void release(uint16_t, uint16_t cid) override { released.push_back(cid); }
  size_t stats(char* buffer, size_t capacity) override {
    const char value[] = "evicted=0/0 truncated=0/0 failed=0 unavailable=0/0";
    assert(capacity >= sizeof(value)); memcpy(buffer, value, sizeof(value)); return sizeof(value)-1;
  }
  void wake() override {}
  void schedule(uint32_t, bool) override {}
};

static ble_evt_t event(uint16_t type, uint16_t cid = 64) {
  ble_evt_t e{}; e.header.evt_id = type;
  e.evt.l2cap_evt.conn_handle = 1; e.evt.l2cap_evt.local_cid = cid; return e;
}
static void request(Server& s, uint16_t cid, uint16_t psm, uint16_t mtu = 558) {
  auto e=event(BLE_L2CAP_EVT_CH_SETUP_REQUEST,cid);
  e.evt.l2cap_evt.params.ch_setup_request.le_psm=psm;
  e.evt.l2cap_evt.params.ch_setup_request.tx_params.tx_mtu=mtu;
  s.event(e);
}
static void setup(Server& s, uint16_t cid) {
  auto e=event(BLE_L2CAP_EVT_CH_SETUP,cid);
  e.evt.l2cap_evt.params.ch_setup.tx_params.tx_mtu=558; s.event(e);
}
static void returned(Server& s, const FakePort::Tx& tx, bool release=false) {
  auto e=event(release?BLE_L2CAP_EVT_CH_SDU_BUF_RELEASED:BLE_L2CAP_EVT_CH_TX,tx.cid);
  if(release) e.evt.l2cap_evt.params.ch_sdu_buf_released.sdu_buf={const_cast<uint8_t*>(tx.pointer),uint16_t(tx.bytes.size())};
  else e.evt.l2cap_evt.params.tx.sdu_buf={const_cast<uint8_t*>(tx.pointer),uint16_t(tx.bytes.size())};
  s.event(e);
}
static void input(Server& s, FakePort& p, uint16_t cid, Kind kind, uint32_t id,
                  const uint8_t* bytes=nullptr, size_t n=0, uint64_t session=123) {
  uint8_t* buffer=nullptr;
  for(const auto& rx:p.receives) if(rx.cid==cid) buffer=rx.pointer;
  assert(buffer);
  const auto len=encode(kind,session,id,bytes,n,buffer,558); assert(len);
  auto e=event(BLE_L2CAP_EVT_CH_RX,cid);
  e.evt.l2cap_evt.params.rx.sdu_len=len;
  e.evt.l2cap_evt.params.rx.sdu_buf={buffer,uint16_t(len)}; s.event(e);
}
static void handshake(Server& s, FakePort& p, Queue& q, uint16_t cid, uint16_t psm, uint32_t now=0) {
  request(s,cid,psm); s.service(&q,123,now); setup(s,cid); s.service(&q,123,now+1);
  assert(!p.sent.empty()); auto hello=p.sent.back();
  assert(hello.cid==cid && hello.bytes[1]==uint8_t(Kind::Hello));
  const uint8_t limit[]={0x20,2};
  input(s,p,cid,Kind::Ready,0,limit,2); returned(s,hello);
  s.service(&q,123,now+2);
}

static void authorizationAndAdmission() {
  FakePort p; Server s(p,1); Queue q(true,123);
  p.status=BLE_L2CAP_CH_STATUS_CODE_INSUFF_AUTHENTICATION;
  request(s,64,0x80); s.service(&q,123,0);
  assert(p.accepts==0 && p.refused.back()==p.status);
  p.status=BLE_L2CAP_CH_STATUS_CODE_SUCCESS;
  request(s,64,0x80,557); s.service(&q,123,1);
  assert(p.accepts==0);
  request(s,64,0x90); assert(p.accepts==0);
  request(s,64,0x80); request(s,65,0x80); s.service(&q,123,2);
  assert(p.accepts==1 && p.refused.size()>=3);
  setup(s,64); s.service(&q,123,3);
  assert(p.sent.back().bytes[1]==uint8_t(Kind::Hello));
  p.status=BLE_L2CAP_CH_STATUS_CODE_INSUFF_AUTHORIZATION;
  s.service(&q,123,4); assert(!p.released.empty());
}

static void singleChannelRecordAndReply() {
  FakePort p; Server s(p,1); Queue q(true,123);
  const uint8_t record[]="RAW synthetic\n"; assert(q.enqueue(record,sizeof(record)-1));
  request(s,64,0x80); s.service(&q,123,0); setup(s,64); s.service(&q,123,1);
  assert(p.sent.size()==1 && p.sent[0].bytes[1]==uint8_t(Kind::Hello));
  const uint8_t limit[]={0x20,2}; input(s,p,64,Kind::Ready,0,limit,2);
  returned(s,p.sent[0]); s.service(&q,123,2);
  assert(p.sent.back().bytes[1]==uint8_t(Kind::Record));
  auto data=p.sent.back(); const auto original=data.bytes;
  input(s,p,64,Kind::Receipt,1); s.service(&q,123,3);
  assert(s.pending() && memcmp(data.pointer,original.data(),original.size())==0);
  returned(s,data); s.service(&q,123,4); assert(!s.pending());
  const uint8_t cmd[]="get prv.key";
  input(s,p,64,Kind::Command,7,cmd,sizeof(cmd)-1); s.service(&q,123,5);
  char command[96]{}; assert(s.takeCommand(command,sizeof(command)));
  assert(strcmp(command,"get prv.key")==0);
  const uint8_t key[]="SYNTHETIC-SECRET";
  assert(s.reply(key,sizeof(key)-1,6)); s.service(&q,123,6);
  assert(q.empty());
  Packet reply; assert(decode(p.sent.back().bytes.data(),p.sent.back().bytes.size(),Direction::ToHost,reply));
  assert(reply.kind==Kind::Reply && reply.id==7 && reply.length==sizeof(key)-1);
  returned(s,p.sent.back()); s.service(&q,123,7);
  const uint8_t bad[]="reboot";
  input(s,p,64,Kind::Command,8,bad,sizeof(bad)-1); s.service(&q,123,8);
  assert(!s.takeCommand(command,sizeof(command)) && !p.released.empty());
}

static void twoChannelControlIsolation() {
  FakePort p; Server s(p,2); Queue q(true,123);
  // Retain the historical control-first sequence as well.
  handshake(s,p,q,65,0x81,10); assert(!s.ready());
  handshake(s,p,q,64,0x80,20); assert(s.ready());
  const uint8_t record[]="RAW separate\n"; assert(q.enqueue(record,sizeof(record)-1));
  s.service(&q,123,30); auto data=p.sent.back(); assert(data.cid==64);
  input(s,p,65,Kind::StatsRequest,1); s.service(&q,123,31);
  assert(p.sent.back().cid==65 && p.sent.back().bytes[1]==uint8_t(Kind::StatsReply));
  assert(s.pending()); // Control progresses while data TX remains vendor-owned.
  auto control=p.sent.back(); returned(s,control);
  input(s,p,65,Kind::Receipt,1); s.service(&q,123,32);
  assert(s.pending()); returned(s,data); s.service(&q,123,33); assert(!s.pending());
  input(s,p,64,Kind::Receipt,1); s.service(&q,123,34);
  assert(p.released.size()==2); // Wrong role fails closed as a pair.
}

static void twoChannelDataFirstMatchesHelperStartup() {
  FakePort p; Server s(p,2); Queue q(true,123);
  const uint8_t record[]="queued until both READY\n"; assert(q.enqueue(record,sizeof(record)-1));

  // Rust helper opens data PSM first, then control PSM. Firmware must admit the
  // first authorized channel while keeping application record TX gated.
  request(s,64,0x80); s.service(&q,123,0);
  assert(p.accepts==1 && p.refused.empty());
  setup(s,64); s.service(&q,123,1);
  assert(p.sent.size()==1 && p.sent[0].cid==64 && p.sent[0].bytes[1]==uint8_t(Kind::Hello));
  const uint8_t limit[]={0x20,2}; input(s,p,64,Kind::Ready,0,limit,2);
  returned(s,p.sent[0]); s.service(&q,123,2);
  assert(!s.ready() && p.sent.size()==1 && s.pending()==false);

  request(s,65,0x81); s.service(&q,123,3);
  assert(p.accepts==2 && p.refused.empty());
  setup(s,65); s.service(&q,123,4);
  assert(p.sent.back().cid==65 && p.sent.back().bytes[1]==uint8_t(Kind::Hello));
  input(s,p,65,Kind::Ready,0,limit,2); returned(s,p.sent.back()); s.service(&q,123,5);
  assert(s.ready());
  assert(p.sent.back().cid==64 && p.sent.back().bytes[1]==uint8_t(Kind::Record));
}

static void timeoutsAndDisconnectOwnership() {
  FakePort p; Server s(p,1); Queue q(true,123);
  handshake(s,p,q,64,0x80);
  const uint8_t record[]="retained\n"; assert(q.enqueue(record,sizeof(record)-1));
  s.service(&q,123,10); auto data=p.sent.back();
  s.service(&q,123,15010); assert(!p.released.empty() && s.pending());
  auto lost=event(BLE_L2CAP_EVT_CH_RELEASED); s.event(lost);
  request(s,66,0x80); s.service(&q,123,15011);
  assert(p.accepts==1); // Cannot reuse buffers while TX or RX still owned.
  returned(s,data,true);
  auto rx=event(BLE_L2CAP_EVT_CH_SDU_BUF_RELEASED);
  rx.evt.l2cap_evt.params.ch_sdu_buf_released.sdu_buf={p.receives.back().pointer,558}; s.event(rx);
  s.service(&q,123,15012);
  handshake(s,p,q,66,0x80,16000);
  assert(p.sent.back().bytes[1]==uint8_t(Kind::Record));
  assert(s.pending());
}

static void rxThenReleaseBeforeLoopServiceIsValid() {
  FakePort p; Server s(p,1); Queue q(true,123);
  handshake(s,p,q,64,0x80);
  const uint8_t command[]="get time";
  input(s,p,64,Kind::Command,1,command,sizeof(command)-1);
  auto released=event(BLE_L2CAP_EVT_CH_RELEASED); s.event(released);
  s.service(&q,123,10);
  assert(s.faults()==0);
  char out[96]{};
  assert(!s.takeCommand(out,sizeof(out)));
}

static void duplicateRxBufferReturnIsInvalid() {
  FakePort p; Server s(p,1); Queue q(true,123);
  handshake(s,p,q,64,0x80);
  const uint8_t command[]="get time";
  input(s,p,64,Kind::Command,1,command,sizeof(command)-1);
  auto duplicate=event(BLE_L2CAP_EVT_CH_SDU_BUF_RELEASED);
  duplicate.evt.l2cap_evt.params.ch_sdu_buf_released.sdu_buf={p.receives.back().pointer,558};
  s.event(duplicate);
  s.service(&q,123,10);
  assert(s.faults()!=0);
}

int main() {
  authorizationAndAdmission(); singleChannelRecordAndReply();
  twoChannelControlIsolation(); twoChannelDataFirstMatchesHelperStartup();
  timeoutsAndDisconnectOwnership();
  rxThenReleaseBeforeLoopServiceIsValid(); duplicateRxBufferReturnIsInvalid();
  assert(sizeof(Server)<4096);
  printf("actual Nordic event structures; secure roles/buffers/commands sizeof=%zu PASS\n",sizeof(Server));
}
