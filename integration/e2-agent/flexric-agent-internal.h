#pragma once

// FlexRIC-specific validation seam used by the focused subscription tests.
#include "lib/e2ap/ric_subscription_request_wrapper.h"

#ifdef __cplusplus
extern "C" {
#endif
int hero_kpm_subscription_supported(const ric_subscription_request_t *request,
                                    uint32_t *period_ms);
#ifdef __cplusplus
}
#endif
