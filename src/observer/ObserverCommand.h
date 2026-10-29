#pragma once

#include <stdint.h>
#include <string.h>

namespace observer {

inline bool parseCanonicalEpoch(const char* command, uint32_t& epoch) {
  if (strncmp(command, "time ", 5) != 0 || command[5] == 0) return false;
  const char* digits = command + 5;
  if (digits[0] == '0' && digits[1] != 0) return false;
  uint64_t parsed = 0;
  for (const char* current = digits; *current; ++current) {
    if (*current < '0' || *current > '9') return false;
    parsed = parsed * 10 + static_cast<uint8_t>(*current - '0');
    if (parsed > UINT32_MAX) return false;
  }
  epoch = static_cast<uint32_t>(parsed);
  return true;
}

inline bool isAllowedReadCommand(const char* command) {
  static const char* const allowed[] = {
      "get name", "get public.key", "get prv.key", "get radio", "ver", "board",
      "stats-core", "stats-radio", "stats-packets"};
  for (const char* candidate : allowed) {
    if (strcmp(command, candidate) == 0) return true;
  }
  return false;
}

}  // namespace observer
