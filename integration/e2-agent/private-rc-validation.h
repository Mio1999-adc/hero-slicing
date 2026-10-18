#pragma once

#include "sm/rc_sm/ie/rc_data_ie.h"
#include <cstdint>

namespace hero::oran::detail {
// Validates only the explicitly private HERO RC payload. It does not assert
// that this control style or its identifiers are standardized.
bool ValidatePrivateRcControlRequest(const rc_ctrl_req_data_t* request,
                                     std::int64_t& bwp,
                                     std::int64_t& maxDlMcs);
}
