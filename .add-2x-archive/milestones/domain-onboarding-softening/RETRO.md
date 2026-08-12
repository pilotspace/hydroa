════════════════════════════════════════════════════════════════════════
 domain-onboarding-softening · Domain onboarding softening: progressive trust ladder (soft member-verified rung + invite-by-domain + DNS-flow softeners)
════════════════════════════════════════════════════════════════════════
 VERDICT   DONE
 TASKS     7/7 done           CRITERIA  7/7 met
 GATES     7 PASS             WAIVERS   none

 goal  A new admin can start using their workspace and invite their team
       by verified email domain the moment they sign up — without first
       completing DNS-TXT — while automatic stranger-join stays strictly
       gated on DNS-verified domain ownership
 closed by Tin Dang <tindang.ht97@gmail.com>

 TASK                        PHASE     GATE TESTS PROGRESS
 ───────────────────────────────────────────────────────────────────────
 dns-verify-softeners        done      PASS 0     ●●●●●●●●●
 domain-verify-notify        done      PASS 0     ●●●●●●●●●
 registrar-hint              done      PASS 0     ●●●●●●●●●
 member-verified-recognition done      PASS 0     ●●●●●●●●●
 member-verified-code-entry  done      PASS 0     ●●●●●●●●●
 invite-by-domain            done      PASS 0     ●●●●●●●●●
 invite-by-domain-ui         done      PASS 0     ●●●●●●●●●
 legend  ● reached  ◉ current  ○ pending   spec→…→done

 GATED BY
   dns-verify-softeners     PASS Tin Dang <tindang.ht97@gmail.com>
   domain-verify-notify     PASS Tin Dang <tindang.ht97@gmail.com>
   registrar-hint           PASS Tin Dang <tindang.ht97@gmail.com>
   member-verified-recogni… PASS Tin Dang <tindang.ht97@gmail.com>
   member-verified-code-en… PASS Tin Dang <tindang.ht97@gmail.com>
   invite-by-domain         PASS Tin Dang <tindang.ht97@gmail.com>
   invite-by-domain-ui      PASS Tin Dang <tindang.ht97@gmail.com>

 EXIT CRITERIA  ●●●●●●●●●● 7/7 met

 LEARNINGS      none

 SPEC DELTAS    279 open deltas — resolve: new-task --from-delta / drop-delta

 DECIDE NEXT  consolidate learnings + archive-milestone
              domain-onboarding-softening
════════════════════════════════════════════════════════════════════════