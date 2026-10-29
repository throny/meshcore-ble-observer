#include "observer/coc/Delivery.h"
#ifdef COC_QUEUE_INCLUDE
#include COC_QUEUE_INCLUDE
#else
#include "observer/ObserverRecordQueue.h"
#endif
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <type_traits>

using observer::coc::Delivery;
using observer::coc::Action;
using Queue = observer::RecordQueue<2048, 16>;

static_assert(!std::is_copy_constructible<Delivery>::value, "pending storage must not be copied");
static_assert(!std::is_copy_assignable<Delivery>::value, "owned storage must not be overwritten");
static_assert(!std::is_move_constructible<Delivery>::value, "vendor pointer must not move");
static_assert(!std::is_move_assignable<Delivery>::value, "vendor pointer must not relocate");

static void load(Delivery& delivery, Queue& queue, uint32_t now = 0) {
  const uint8_t bytes[] = {'r', 'a', 'w', '\n'};
  assert(queue.enqueue(bytes, sizeof(bytes)));
  assert(delivery.load(queue));
  assert(delivery.ready(now));
  assert(queue.empty());
}

static void consecutiveRecords() {
  Delivery d;
  Queue q(true, 123);
  assert(!d.open(0)); assert(d.open(1)); assert(!d.open(2));
  for (unsigned n = 1; n <= 1000; ++n) {
    load(d, q);
    assert(d.sequence() == n && d.session() == 123 && d.attempts() == 0);
    assert(!d.receipt(1, 123, n));  // No receipt for a never-submitted record.
    const uint8_t* p = d.data();
    assert(d.submitAccepted(1, 0));
    assert(!d.ready(1));
    assert(!d.receipt(1, 123, n + 1));
    if (n % 2) {
      assert(d.receipt(1, 123, n));
      assert(d.pending() && d.vendorOwned());
      assert(d.returned(1, p, 1));
    } else {
      assert(d.returned(1, p, 1));
      assert(d.pending() && !d.vendorOwned());
      assert(d.receipt(1, 123, n));
    }
    assert(!d.pending() && !d.data());
  }
}

static void ownershipAndGeneration() {
  Delivery d; Queue q(true, 123); assert(d.open(1)); load(d, q);
  const uint8_t* p = d.data();
  assert(d.submitAccepted(1, 0));
  assert(!d.returned(1, p + 1, 1));
  assert(!d.returned(2, p, 1));
  assert(!d.receipt(2, 123, 1)); assert(!d.receipt(1, 124, 1));
  d.close();
  assert(!d.open(2)); assert(d.pending());
  assert(!d.receipt(1, 123, 1));
  assert(d.returned(1, p, 2)); assert(!d.returned(1, p, 3));
  assert(!d.open(1)); assert(d.open(2));
  assert(d.sequence() == 1 && d.session() == 123 && d.ready(3));
  assert(d.submitAccepted(2, 3));
  assert(!d.returned(1, p, 4));  // Same address does not defeat generation fence.
  assert(d.receipt(2, 123, 1));
  assert(d.returned(2, p, 4));
  load(d, q, 5); assert(d.sequence() == 2);
  assert(!d.receipt(2, 123, 1));
}

static void retryAndResourceDeadlines() {
  Delivery d; Queue q(true, 123); assert(d.open(1)); load(d, q);
  for (unsigned n = 0; n < 3; ++n) {
    const uint32_t now = n * 6000;
    assert(d.submitAccepted(1, now));
    assert(d.returned(1, d.data(), now + 1));
    assert(!d.ready(now + 5001));
    const Action action = d.poll(now + 5001);
    assert(action == (n == 2 ? Action::Close : Action::Retry));
  }
  assert(d.closed() && d.pending() && d.attempts() == 3);
  assert(d.open(2) && d.attempts() == 0);
  assert(d.resourcesBusy(2, 20000));
  for (uint32_t now = 20001; now < 35000; now += 1000) {
    assert(d.resourcesAvailable(2));
    assert(d.resourcesBusy(2, now));
  }
  uint32_t delay = 0;
  assert(d.wakeDelay(34999, delay) && delay == 1);
  assert(!d.ready(35000)); assert(d.poll(35000) == Action::Close);
  assert(d.pending() && !d.vendorOwned());

  assert(d.open(3));
  assert(d.submitAccepted(3, 40000)); assert(d.returned(3, d.data(), 40001));
  assert(d.poll(45001) == Action::Retry);
  assert(d.resourcesBusy(3, 46000));
  assert(d.resourcesAvailable(3)); assert(d.resourcesBusy(3, 60000));
  assert(d.poll(61000) == Action::Close);  // Retry cannot renew progress stall.

  assert(d.open(4)); assert(d.resourcesBusy(4, 70000)); assert(d.resourcesAvailable(4));
  assert(!d.submitAccepted(4, 85000));
  assert(d.poll(85000) == Action::Close);
}

static void timerWrapAndSleep() {
  Delivery d; Queue q(true, 123); assert(d.open(1)); load(d, q);
  const uint32_t start = UINT32_MAX - 10000;
  assert(d.submitAccepted(1, start));
  uint32_t delay = 0;
  assert(d.wakeDelay(start + 14999u, delay) && delay == 1);
  for (uint32_t dt = 0; dt < 15000; dt += 100) assert(!d.ready(start + dt));
  assert(d.poll(start + 14999u) == Action::Idle);
  assert(d.poll(start + 15000u) == Action::Close);
  assert(d.vendorOwned() && d.pending());
  assert(!d.wakeDelay(1, delay));
  assert(d.returned(1, d.data(), 5000));
  assert(d.open(2));
  assert(d.submitAccepted(2, start)); assert(d.returned(2, d.data(), start));
  assert(d.wakeDelay(start + 4999u, delay) && delay == 1);
  assert(d.poll(start + 5000u) == Action::Retry);
}

static void queueHandoffAndEviction() {
  Delivery d; Queue q(true, 123); assert(d.open(1));
  uint8_t payload[COC_QUEUE_LIMIT]{};
  uint8_t replacement[COC_QUEUE_LIMIT]{};
  for (unsigned n = 0; n < 1000; ++n) {
    const size_t length = n % COC_QUEUE_LIMIT + 1;
    memset(payload, static_cast<uint8_t>(n), length);
    assert(q.enqueue(payload, length)); assert(d.load(q));
    assert(d.size() == length + 14);
    assert(memcmp(d.data() + 14, payload, length) == 0);
    const auto sequence = d.sequence();
    // Queue advances and evicts while the pending SDU remains vendor-owned.
    assert(d.submitAccepted(1, 0));
    memset(replacement, static_cast<uint8_t>(n + 1), sizeof(replacement));
    for (unsigned k = 0; k < 20; ++k) assert(q.enqueue(replacement, COC_QUEUE_LIMIT));
    assert(!d.load(q));
    assert(memcmp(d.data() + 14, payload, length) == 0);
    assert(d.receipt(1, 123, sequence));
    assert(d.returned(1, d.data(), 1));
    // Drain backlog through exact local queue ownership transfer, not receipt
    // against a later queue head.
    while (!q.empty()) {
      assert(d.load(q));
      assert(d.submitAccepted(1, 2));
      assert(d.returned(1, d.data(), 3));
      assert(d.receipt(1, d.session(), d.sequence()));
    }
  }
  assert(q.evictedRecords() > 0);
  Queue bad_nonce(true, 0);
  assert(bad_nonce.enqueue(payload, 1));
  assert(!d.load(bad_nonce)); assert(bad_nonce.size() == 1);
}

int main() {
  consecutiveRecords(); ownershipAndGeneration(); retryAndResourceDeadlines();
  timerWrapAndSleep(); queueHandoffAndEviction();
  assert(sizeof(Delivery) <= 640);  // Not a total firmware/SoftDevice RAM claim.
  printf("delivery queue_limit=%u sizeof=%zu ownership/deadlines/wrap PASS\n",
         unsigned(COC_QUEUE_LIMIT), sizeof(Delivery));
}
