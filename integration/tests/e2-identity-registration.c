#include "e2-agent/flexric-agent.h"
#include <stdio.h>
int main(void) {
  hero_e2_config c={.ric_ip="127.0.0.1",.port=36421,.mcc=505,.mnc=1,.mnc_digits=2,.gnb_id=101,.connect_timeout_ms=1000};
  if(!hero_e2_identity_registration_check(&c)) return 1;
  c.mcc=310; c.mnc=260; c.mnc_digits=3; c.gnb_id=0x1fffffff;
  if(!hero_e2_identity_registration_check(&c)) return 2;
  c.mcc=1; c.mnc=1; c.mnc_digits=2; c.gnb_id=1;
  if(!hero_e2_identity_registration_check(&c)) return 3;
  c.mnc=100;
  if(hero_e2_identity_registration_check(&c)) return 4;
  c.mnc=1; c.gnb_id=0;
  if(hero_e2_identity_registration_check(&c)) return 5;
  puts("PASS: E2AP identity roundtrips, KPM ID=2 revision=3 definition, absent report/measurement capabilities, invalid identity rejection");
}
