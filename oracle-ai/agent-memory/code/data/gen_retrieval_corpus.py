#!/usr/bin/env python3
# v1.0 2026-10-10  Brief 12 retrieval corpus and labelled queries (PLAN.md section 3, 7.1, 7.3): one fictional business
#                  account BIZ_0001 with 400 memories over two years, 120 C1 queries (40 per class A, B, C) and 40 C3
#                  set queries, all labelled from the generator's own records. Seeded and deterministic; frozen in 7.0.
#                  v1.0: first version (day 0, no database, no LLM).
"""Generate data/frozen/c1_corpus.jsonl, c1_queries.jsonl and c3_queries.jsonl.

Run as : the authoring host or the demo VM; no database, no network.
Usage  : python3 data/gen_retrieval_corpus.py [--out DIR] [--check]
           --out    output directory (default: data/frozen next to this file)
           --check  regenerate in memory and compare with the files in --out; exit 1 on any difference
Re-run : safe; the same seed writes byte-identical files (atomic replace).

What it builds (PLAN section 3 and 7.1/7.3):
- 400 memories: 120 ticket notes, 100 plan and add-on changes, 60 device records, 60 billing facts, 60 contact and
  preference notes; 20 topics of 20 memories; every memory has exactly one identifier and every identifier occurs in
  exactly one memory; 30 near-collision identifier pairs (TKT-48213/TKT-48231 and PLN-UNL-50/PLN-UNL-5 among them);
  no other two identifiers are within one edit (optimal string alignment distance >= 2).
- C1: class A, 40 identifier-only queries; class B, 40 paraphrase questions with no identifier, no month and no year that
  share at most one content word with their gold memory (overlap rule below, published per query); class C, 40
  questions that contain an identifier. One gold memory per query; the 120 gold memories are distinct. 15 class A and
  15 class C queries target an identifier that has a near-collision sibling.
- C3: 40 set questions "<topic> in <month>", whose gold set is every memory generated with that topic and month
  (2-5 by construction).

Overlap rule (class B): content words are the lower-case alphanumeric tokens that are not in STOPWORDS (digits always
count, other tokens need two characters), reduced by stem(); the overlap of a query is the number of distinct stems it
shares with its gold memory's full text.
"""
from __future__ import annotations

import argparse
import calendar
import json
import logging
import os
import random
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

LOG = logging.getLogger("gen_retrieval_corpus")

SEED = 20261004
USER_ID = "BIZ_0001"
OUT_DEFAULT = Path(__file__).resolve().parent / "frozen"
FILES = ("c1_corpus.jsonl", "c1_queries.jsonl", "c3_queries.jsonl")

MONTHS = [(2024 + (9 + i) // 12, (9 + i) % 12 + 1) for i in range(24)]   # Oct 2024 .. Sep 2026

KIND_COUNTS = {"ticket": 120, "plan": 100, "device": 60, "billing": 60, "contact": 60}
TOPICS = {
    "ticket": ("roaming_dispute", "network_outage", "sim_replacement", "number_porting", "voicemail_fault",
               "intl_call_block"),
    "plan": ("plan_upgrade", "plan_downgrade", "roaming_addon", "data_pack", "addon_cancel"),
    "device": ("device_issued", "device_warranty", "device_returned"),
    "billing": ("credit_note", "late_fee_waiver", "debit_failed"),
    "contact": ("channel_pref", "billing_contact", "quiet_hours"),
}
PER_TOPIC = 20
TOPIC_KIND = {t: k for k, ts in TOPICS.items() for t in ts}

# C3 question label of each topic (a plural noun phrase).
C3_LABEL = {
    "roaming_dispute": "roaming charge disputes", "network_outage": "network outages",
    "sim_replacement": "SIM card replacements", "number_porting": "number porting requests",
    "voicemail_fault": "voicemail faults", "intl_call_block": "international calling blocks",
    "plan_upgrade": "plan upgrades", "plan_downgrade": "plan downgrades",
    "roaming_addon": "roaming add-on activations", "data_pack": "data top-up pack purchases",
    "addon_cancel": "add-on cancellations", "device_issued": "handset issuances",
    "device_warranty": "warranty device replacements", "device_returned": "device returns",
    "credit_note": "credit notes", "late_fee_waiver": "late payment fee waivers",
    "debit_failed": "failed direct debits", "channel_pref": "contact channel changes",
    "billing_contact": "billing contact changes", "quiet_hours": "quiet hours requests",
}
C3_TEMPLATES = ("Which {label} were recorded in {month}?", "Show all {label} from {month}.",
                "List all {label} in {month}.")

SITES = ("north depot", "harbour office", "airport road branch", "west warehouse", "head office", "logistics yard",
         "city showroom", "call centre", "training centre", "south workshop", "east depot", "staff housing block")
COUNTRIES = ("Oman", "Saudi Arabia", "the UAE", "Kuwait", "Bahrain", "Jordan", "Egypt", "Turkey", "India",
             "the UK")
MODELS = ("Zento X2", "Kairo S5", "Brisk 7", "Mirel A4", "Orin 12", "Talo Mini", "Vexa Pro", "Quill 9")
CHANNELS = ("e-mail", "SMS", "WhatsApp", "post", "the account portal")
ROLES = ("finance manager", "office manager", "procurement lead", "fleet coordinator", "operations director")
WINDOWS = ("before 08:00", "after 20:00", "on Fridays", "during the lunch hour", "on public holidays")

# Header phrasings per topic. Placeholders: id, date, line, site, amt, model, channel, role, window, country.
HEADERS = {
    "roaming_dispute": ("Ticket {id} was opened on {date} to dispute a roaming charge of QAR {amt} on line {line}.",
                        "On {date} the account raised ticket {id}, disputing roaming fees of QAR {amt} on line {line}.",
                        "Roaming charge dispute {id}, logged on {date}: QAR {amt} billed to line {line}."),
    "network_outage": ("Ticket {id} was opened on {date} for a network outage at the {site}.",
                       "On {date} ticket {id} reported a total loss of mobile service at the {site}.",
                       "Network outage {id}, logged on {date}: no signal at the {site}."),
    "sim_replacement": ("Ticket {id} was opened on {date} for a SIM card replacement on line {line}.",
                        "On {date} ticket {id} asked for a replacement SIM card for line {line}.",
                        "SIM card replacement {id}, logged on {date}, for line {line}."),
    "number_porting": ("Ticket {id} was opened on {date} for a number porting request on line {line}.",
                       "On {date} ticket {id} asked to port the number of line {line}.",
                       "Number porting request {id}, logged on {date}, for line {line}."),
    "voicemail_fault": ("Ticket {id} was opened on {date} for a voicemail fault on line {line}.",
                        "On {date} ticket {id} reported a voicemail fault on line {line}.",
                        "Voicemail fault {id}, logged on {date}, on line {line}."),
    "intl_call_block": ("Ticket {id} was opened on {date} because international calls were blocked on line {line}.",
                        "On {date} ticket {id} reported an international calling block on line {line}.",
                        "International calling block {id}, logged on {date}, on line {line}."),
    "plan_upgrade": ("On {date} line {line} was upgraded to plan {id}.",
                     "Plan upgrade on {date}: line {line} moved up to plan {id}.",
                     "Line {line} upgraded its plan to {id} on {date}."),
    "plan_downgrade": ("On {date} line {line} was downgraded to plan {id}.",
                       "Plan downgrade on {date}: line {line} moved down to plan {id}.",
                       "Line {line} downgraded its plan to {id} on {date}."),
    "roaming_addon": ("On {date} the roaming add-on {id} was added to line {line}.",
                      "Roaming add-on {id} was activated on line {line} on {date}.",
                      "Line {line} got the roaming add-on {id} on {date}."),
    "data_pack": ("On {date} the data top-up pack {id} was added to line {line}.",
                  "Data top-up pack {id} was bought for line {line} on {date}.",
                  "Line {line} received the data top-up pack {id} on {date}."),
    "addon_cancel": ("On {date} the add-on {id} was cancelled on line {line}.",
                     "Add-on cancellation on {date}: {id} was removed from line {line}.",
                     "Line {line} had its add-on {id} cancelled on {date}."),
    "device_issued": ("On {date} a {model} handset with asset tag {id} was issued to line {line}.",
                      "Handset issued on {date}: {model}, asset tag {id}, for line {line}.",
                      "Line {line} was issued the {model} handset {id} on {date}."),
    "device_warranty": ("On {date} the {model} with asset tag {id} on line {line} was replaced under warranty.",
                        "Warranty replacement on {date}: the {model}, asset tag {id}, on line {line}.",
                        "The {model} {id} used on line {line} was swapped under warranty on {date}."),
    "device_returned": ("On {date} the {model} with asset tag {id} was returned from line {line}.",
                        "Device returned on {date}: the {model}, asset tag {id}, from line {line}.",
                        "The {model} {id} came back from line {line} on {date}."),
    "credit_note": ("On {date} a credit note of QAR {amt} was issued against invoice {id}.",
                    "Credit note issued on {date}: QAR {amt} against invoice {id}.",
                    "Invoice {id} received a credit note of QAR {amt} on {date}."),
    "late_fee_waiver": ("On {date} the late payment fee of QAR {amt} on invoice {id} was waived.",
                        "Late payment fee waived on {date}: QAR {amt} on invoice {id}.",
                        "The QAR {amt} late payment fee on invoice {id} was waived on {date}."),
    "debit_failed": ("On {date} the direct debit for invoice {id}, QAR {amt}, failed.",
                     "Failed direct debit on {date}: invoice {id} for QAR {amt}.",
                     "The direct debit collection of QAR {amt} for invoice {id} failed on {date}."),
    "channel_pref": ("Note {id}, {date}: the account changed its preferred contact channel to {channel}.",
                     "On {date} note {id} recorded a new preferred contact channel: {channel}.",
                     "Contact channel change on {date} (note {id}): from now on, {channel}."),
    "billing_contact": ("Note {id}, {date}: the billing contact for the account changed to the {role}.",
                        "On {date} note {id} recorded a billing contact change to the {role}.",
                        "Billing contact change on {date} (note {id}): the {role} takes over."),
    "quiet_hours": ("Note {id}, {date}: the account asked for no calls {window}.",
                    "On {date} note {id} recorded a quiet hours request: no calls {window}.",
                    "Quiet hours request on {date} (note {id}): no calls {window}."),
}

# Generic detail sentences per topic (mundane, so that no generic memory resembles a class B signature).
DETAILS = {
    "roaming_dispute": ("The charge came from a business trip to {country}.",
                        "Data had been used while the phone was in {country}.",
                        "The calls were made during a short stay in {country}.",
                        "The traveller said the roaming pack had already been bought for {country}.",
                        "The fees appeared after a conference in {country}.",
                        "The roaming session was billed for {country} at the standard rate."),
    "network_outage": ("Service was down for about three hours.", "The outage hit during the morning shift.",
                       "All lines on site lost service at once.", "The outage started after a nearby mast lost power.",
                       "Only voice calls failed; the site switched to landlines.",
                       "The outage lasted most of the afternoon."),
    "sim_replacement": ("The old SIM had stopped being detected by the phone.", "The handset with the SIM was lost.",
                        "The user wanted to move to an eSIM.", "The SIM was damaged during a handset repair.",
                        "The SIM showed a PIN lock after too many attempts.",
                        "A new handset needed a smaller SIM size."),
    "number_porting": ("The number was coming in from another operator.", "The number was moving out to another operator.",
                       "The number was being moved from a prepaid account.",
                       "The number was being merged into the business account.",
                       "The port was requested for a new branch manager.",
                       "The port was needed after a supplier contract ended."),
    "voicemail_fault": ("Voicemail was not recording any messages.", "The voicemail PIN no longer worked.",
                        "Callers were sent straight to a busy tone instead of voicemail.",
                        "Voicemail notifications stopped arriving.", "The mailbox was reported as full every day.",
                        "Old messages could not be deleted."),
    "intl_call_block": ("Calls to {country} failed with an error tone.",
                        "The block started after a credit limit was reached.",
                        "International dialling had been switched off by mistake.",
                        "Only calls to mobile numbers abroad were blocked.",
                        "The block was set when the line was first activated.",
                        "Calls abroad stopped without any warning."),
    "plan_upgrade": ("The previous plan kept running out of data mid-month.", "The higher tier was needed for video calls.",
                     "The upgrade was requested by the operations manager.",
                     "The change took effect at the start of the next bill cycle.",
                     "The new plan adds more data for field work.", "The upgrade came with a 12-month commitment."),
    "plan_downgrade": ("Usage had dropped after the team moved to office work.", "The line is now used mainly for calls.",
                       "The downgrade was part of a cost review.", "The user moved to a shared tablet for data.",
                       "The change took effect at the start of the next bill cycle.",
                       "The lower tier still covers the monthly usage."),
    "roaming_addon": ("It was needed for a trip to {country}.", "It covers data and calls in {country} for a week.",
                      "The traveller is visiting a supplier in {country}.",
                      "It was added before a trade show in {country}.", "It covers a two-week project in {country}.",
                      "It was requested by the fleet coordinator for a drive to {country}."),
    "data_pack": ("The line had used its monthly allowance early.", "Extra data was needed for a software update rollout.",
                  "The pack covers the rest of the month.", "The user was working from a site without Wi-Fi.",
                  "The pack was bought before a long field trip.", "Video meetings had used up the allowance."),
    "addon_cancel": ("It was no longer needed after the trip ended.", "The team stopped using it.",
                     "The user said it was never used.", "It was cancelled as part of a cost review.",
                     "The line was being closed down.", "It had renewed by mistake."),
    "device_issued": ("It went to a new driver in the fleet team.", "It took over from an old desk phone.",
                      "It was issued to a new hire in sales.", "It was set up with the company apps before delivery.",
                      "It is a spare for the on-call engineer.", "It was issued with a protective case."),
    "device_warranty": ("Its screen stopped responding to touch.", "The battery would no longer hold a charge.",
                        "It kept restarting by itself.", "The charging port stopped working.",
                        "The camera showed only a black image.", "It would not connect to any network."),
    "device_returned": ("The employee left the company.", "It was surplus after a team merger.",
                        "The user moved to a different model.", "It was returned at the end of a project.",
                        "It was returned for recycling.", "The line it served was closed."),
    "credit_note": ("It corrected a duplicated monthly fee.", "It covered a mispriced order for the {site}.",
                    "It reversed a charge for an add-on that was never activated.",
                    "It was a goodwill gesture after a complaint.", "It corrected a wrong plan price.",
                    "It refunded a cancelled order."),
    "late_fee_waiver": ("Payment had been delayed by a public holiday.", "The invoice had been sent to an old address.",
                        "It was the first late payment on the account.", "The payment was only one day late.",
                        "The account manager approved it as goodwill.", "The invoice was disputed at the time."),
    "debit_failed": ("The bank reported insufficient funds.", "The direct debit mandate had expired.",
                     "The bank account details had changed.", "The bank rejected the collection without a reason.",
                     "The collection date fell on a bank holiday.", "The account had been closed by the bank."),
    "channel_pref": ("The request came from the office manager.", "The old channel was missing important notices.",
                     "The change applies to all service notices.", "The team said it reads that channel more often.",
                     "The change applies to billing notices only.", "The request was confirmed in writing."),
    "billing_contact": ("The previous contact moved to another department.", "The change follows a new finance structure.",
                        "The new contact asked for all invoices from now on.", "The previous contact left the company.",
                        "The change was confirmed by the director.", "It applies from the next invoice."),
    "quiet_hours": ("The staff work night shifts.", "The request applies to marketing and service calls alike.",
                    "Urgent service calls are still allowed.", "The request came from the office manager.",
                    "It applies to all lines on the account.", "It was asked for after several early calls."),
}
OUTCOMES = {
    "roaming_dispute": ("The charge was refunded in full.", "The charge was upheld after review.",
                        "A partial credit was applied.", "The charge was reversed on the next bill."),
    "network_outage": ("Service was restored by the field team.", "The mast was repaired the same day.",
                       "A temporary cell was deployed on site.", "Service returned after a core network fix."),
    "sim_replacement": ("A new SIM was couriered to the {site}.", "The SIM was swapped at a branch.",
                        "An eSIM profile was issued.", "The replacement was activated the same day."),
    "number_porting": ("The port completed in two days.", "The port was rejected for a name mismatch.",
                       "The port completed after documents were resent.", "The request was withdrawn."),
    "voicemail_fault": ("The mailbox was reset.", "The voicemail profile was rebuilt.",
                        "The fault was fixed by a network update.", "The user was guided to reset the PIN."),
    "intl_call_block": ("The block was lifted the same day.", "The block was lifted after a credit check.",
                        "International dialling was re-enabled.", "The block stayed until the bill was paid."),
    "plan_upgrade": ("The first bill on the new plan was prorated.", "No device change was needed.",
                     "The account manager confirmed the new price.", "The change was confirmed by SMS."),
    "plan_downgrade": ("The first bill on the new plan was prorated.", "No device change was needed.",
                       "The account manager confirmed the new price.", "The change was confirmed by SMS."),
    "roaming_addon": ("It renews only if asked.", "The price was added to the next bill.",
                      "The traveller was sent the usage guide.", "It starts as soon as the phone roams."),
    "data_pack": ("The data was available within minutes.", "The price was added to the next bill.",
                  "It expires at the end of the bill cycle.", "The user was told by SMS."),
    "addon_cancel": ("No refund was due.", "The last month was refunded.", "It stopped at the end of the cycle.",
                     "The user was told by SMS."),
    "device_issued": ("Delivery was confirmed by the user.", "It was delivered to the {site}.",
                      "Setup was done by the IT team.", "It was handed over at a branch."),
    "device_warranty": ("A replacement unit was sent within two days.", "The replacement was handed over at a branch.",
                        "The faulty unit was sent to the repair centre.", "Data was moved to the new unit."),
    "device_returned": ("The unit was wiped and stored.", "The unit was sent for recycling.",
                        "The unit was reissued to another line.", "The unit was checked and found in good order."),
    "credit_note": ("It was applied to the next bill.", "It was paid back to the bank account.",
                    "It was split over two bills.", "The account manager confirmed it."),
    "late_fee_waiver": ("The waiver was shown on the next bill.", "The account manager confirmed it.",
                        "No further fee was charged.", "The waiver was noted on the account."),
    "debit_failed": ("The invoice was paid by bank transfer a week later.", "The collection was retried successfully.",
                     "A new mandate was signed.", "A reminder was sent."),
    "channel_pref": ("The change took effect at once.", "The account manager confirmed it.",
                     "It was set for all lines.", "The old channel was switched off."),
    "billing_contact": ("The change took effect at once.", "The account manager confirmed it.",
                        "The old contact was removed.", "The next invoice went to the new contact."),
    "quiet_hours": ("The request took effect at once.", "The account manager confirmed it.",
                    "It was set for all lines.", "It was added to the call centre notes."),
}

# Class B signatures: (signature id, topic, detail sentence for the gold memory, paraphrase question).
SIGNATURES = (
    ("S01", "roaming_dispute", "The roaming bill came from a cruise ship's onboard network in the Red Sea.",
     "Which complaint involved costs from a vessel's at-sea mobile service?"),
    ("S02", "roaming_dispute",
     "The phone had silently connected to a foreign tower across the border while staying at home near the frontier.",
     "Which case was about a handset latching onto a neighbouring country's mast without ever leaving the house?"),
    ("S03", "roaming_dispute", "The disputed amount was for a satellite messaging session used during a desert rally.",
     "Which complaint concerned off-grid texting through space-based links at a dune motorsport event?"),
    ("S04", "network_outage", "A sandstorm knocked over the rooftop antenna that serves the building.",
     "Which incident was caused by a dust storm toppling the aerial on the roof?"),
    ("S05", "network_outage", "A family of pigeons nesting in the equipment cabinet shorted the power supply.",
     "Which problem was traced to birds building a nest inside the electrical enclosure?"),
    ("S06", "network_outage", "Road crews cut the fibre backhaul while laying a new water pipe.",
     "Which disruption happened when construction workers digging for plumbing severed the optical cable?"),
    ("S07", "sim_replacement", "The SIM was bent when the handset was run over by a forklift.",
     "Which chip swap followed a warehouse lift truck crushing the mobile?"),
    ("S08", "sim_replacement", "A toddler dropped the SIM tray into a fish tank.",
     "Which card swap was needed after a small child let the holder fall into an aquarium?"),
    ("S09", "sim_replacement", "The SIM wore out after years inside a vending machine's telemetry unit.",
     "Which chip had degraded from long service in a snack dispenser's monitoring module?"),
    ("S10", "number_porting", "The number belonged to a closed bakery that the company bought.",
     "Which transfer brought over the contact of an acquired pastry shop?"),
    ("S11", "number_porting", "The line was a vanity number spelling a word on the keypad.",
     "Which transfer involved a memorable digit sequence that reads as text on the dial pad?"),
    ("S12", "voicemail_fault", "The greeting played in a language nobody at the firm speaks.",
     "Which answering service problem had the welcome message in a foreign tongue no staff understood?"),
    ("S13", "voicemail_fault", "Messages vanished after exactly seven days because of a wrong retention setting.",
     "Which mailbox lost recordings after one week due to a misconfigured storage period?"),
    ("S14", "voicemail_fault", "Every caller heard a fax tone instead of the mailbox.",
     "Which answering problem gave people ringing in a facsimile screech?"),
    ("S15", "intl_call_block", "Calls to a supplier abroad were blocked by a fraud rule after a midnight spike.",
     "Which overseas dialling barrier was triggered by an anti-scam filter after late-night volume?"),
    ("S16", "intl_call_block", "The block hit only numbers that began with a zero after the country code.",
     "Which barring affected destinations whose digits started with a nought after the nation prefix?"),
    ("S17", "plan_upgrade", "The extra data was for livestreaming the company's camel race sponsorship.",
     "Which tariff move supported broadcasting a dromedary racing event the firm backed?"),
    ("S18", "plan_upgrade", "The line is in a drone that maps date palm farms.",
     "Which tariff move was for an unmanned aircraft surveying orchards?"),
    ("S19", "plan_downgrade", "The line now only rings the gate intercom at the staff car park.",
     "Which cheaper tariff was for a phone that just buzzes the entrance speaker in the employee parking lot?"),
    ("S20", "plan_downgrade", "The handset was moved to a museum display about the company's history.",
     "Which cheaper tariff followed a mobile being put in an exhibition on the firm's heritage?"),
    ("S21", "roaming_addon", "The pass covered a falconry team flying birds at a contest abroad.",
     "Which travel bundle was for hawk handlers competing at an overseas championship?"),
    ("S22", "data_pack", "The top-up fed a weather station logging humidity on a mangrove survey.",
     "Which extra allowance powered a climate sensor recording moisture in coastal wetlands research?"),
    ("S23", "data_pack", "The extra gigabytes were burned by a dashcam uploading every journey.",
     "Which top-up was consumed by an in-vehicle camera sending each trip to the cloud?"),
    ("S24", "addon_cancel", "It was dropped once the night guards switched to radios.",
     "Which bolt-on ended when security staff on the late shift moved to walkie-talkies?"),
    ("S25", "device_issued", "It is a rugged unit for the divers who inspect the harbour pilings.",
     "Which handset went to underwater crews checking the port's support columns?"),
    ("S26", "device_issued", "The phone runs the scoreboard at the company's football pitch.",
     "Which handset operates the results display at the firm's soccer field?"),
    ("S27", "device_warranty", "The screen cracked from cold when it was left in a freezer truck.",
     "Which unit was swapped after its display shattered in a refrigerated lorry?"),
    ("S28", "device_warranty", "Its speaker failed after it was used as a megaphone at a staff event.",
     "Which phone needed exchanging because its audio output broke from being a loudhailer at an employee gathering?"),
    ("S29", "device_returned", "The worker who had it retired to run an olive grove.",
     "Which handover followed an employee leaving to farm fruit trees in later life?"),
    ("S30", "device_returned", "It came back because the warehouse switched to fixed barcode scanners.",
     "Which gadget was handed in after the depot moved to wall-mounted label readers?"),
    ("S31", "credit_note",
     "It refunded a month of service charged to a line that belonged to a demolished kiosk.",
     "Which billing correction repaid fees for a connection at a booth that was torn down?"),
    ("S32", "credit_note", "The credit compensated for a week of dropped calls during a trade fair.",
     "Which adjustment made up for seven days of cut-off conversations at an exhibition?"),
    ("S33", "late_fee_waiver", "The payment was late because the finance office was flooded.",
     "Which penalty was cancelled since water damage shut the accounts department?"),
    ("S34", "late_fee_waiver", "The delay came from a bank system migration that froze transfers.",
     "Which penalty was forgiven because a lender's platform move halted remittances?"),
    ("S35", "debit_failed", "The collection bounced because the account was frozen during an audit.",
     "Which automatic payment was rejected while the funds were locked for an inspection?"),
    ("S36", "channel_pref", "The team wants updates through the radio dispatcher instead of phones.",
     "Which note asked for news via the two-way relay operator rather than handsets?"),
    ("S37", "channel_pref", "Messages must go to a shared tablet in the site canteen.",
     "Which preference sends communications to a common device in the staff dining hall?"),
    ("S38", "billing_contact", "Invoices now go to the external auditors' office during a takeover.",
     "Which update routed statements to outside inspectors while the business was being acquired?"),
    ("S39", "billing_contact", "The new contact is the warehouse foreman because the accountant is on long leave.",
     "Which update named the depot supervisor as recipient while the bookkeeper is away for months?"),
    ("S40", "quiet_hours", "No calls while the staff attend the weekly prayer and lunch break on Friday.",
     "Which do-not-disturb arrangement covers the end-of-week worship and meal pause?"),
)

# Class C question templates per topic group; {id} is the identifier.
C_TEMPLATES = {
    "ticket": ("What was the outcome of ticket {id}?", "How was {id} resolved?", "What happened with ticket {id}?",
               "Why was ticket {id} raised?"),
    "plan_change": ("Which line moved to plan {id} and when?", "When did plan {id} start on the account?",
                    "Why was the plan changed to {id}?"),
    "addon": ("Which line got add-on {id}?", "When was add-on {id} added?", "What was add-on {id} for?"),
    "addon_cancel": ("Why was add-on {id} cancelled?", "When was {id} cancelled and on which line?"),
    "device": ("What happened to device {id}?", "Which handset model is asset {id}?", "Which line had asset {id}?"),
    "billing": ("What happened with invoice {id}?", "How much was involved on invoice {id}?",
                "What is recorded about invoice {id}?"),
    "contact": ("What does note {id} say?", "What did the account ask for in note {id}?",
                "What changed in note {id}?"),
}
C_GROUP = {"plan_upgrade": "plan_change", "plan_downgrade": "plan_change", "roaming_addon": "addon",
           "data_pack": "addon", "addon_cancel": "addon_cancel"}

# Fixed examples written in PLAN.md (section 3 and 7.1).
NAMED_PAIRS = (("TKT-48213", "TKT-48231", "roaming_dispute", "TKT-48213", "C", "why was TKT-48213 refunded?"),
               ("PLN-UNL-50", "PLN-UNL-5", "plan_upgrade", "PLN-UNL-5", "A", None))

# Near-collision pairs per kind (named pairs included) and query targets per kind and class.
PAIRS_PER_KIND = {"ticket": 10, "plan": 8, "device": 4, "billing": 4, "contact": 4}
PAIR_TARGETS_A = {"ticket": 5, "plan": 4, "device": 2, "billing": 2, "contact": 2}          # 15; class C the same
TARGETS_PER_KIND = {"ticket": 12, "plan": 10, "device": 6, "billing": 6, "contact": 6}      # per class A and C (40)

STOPWORDS = frozenset("""
a about above after again against all also am an and any are as at be because been before being below between both
but by can could did do does doing done down during each either else ever every few for from had has have having he
her here hers herself him himself his how i if in into is it its itself just me more most much must my myself neither
no nor not now of off on once only or other ought our ours ourselves out over own per rather same shall she should so
some still such than that the their theirs them themselves then there these they this those through thus to too under
until up upon us very via was we were what when where whether which while who whom whose why will with within without
would yet you your yours yourself
""".split())

ID_RE = re.compile(r"(?<![A-Za-z0-9-])(?:TKT-\d{5}|DEV-\d{5}|NTE-\d{5}|INV-\d{6}|PLN-[A-Z]{3}-\d{1,2}|ADD-[A-Z]{3}-\d{1,2})"
                   r"(?![A-Za-z0-9-])")
ID_SHAPE_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2,}-[A-Z0-9]")      # anything that looks like a code
MONTH_WORD_RE = re.compile(r"\b(?:" + "|".join(calendar.month_name[1:]) + r")\b|\b20\d\d\b", re.I)
TOKEN_RE = re.compile(r"[a-z0-9]+")


def stem(token: str) -> str:
    """Light, deterministic suffix stripping used only for the class B overlap count (strict: it merges more forms)."""
    t = token
    if len(t) > 4 and t.endswith("ies"):
        t = t[:-3] + "i"
    else:
        for suf in ("ing", "ed", "es", "s"):
            if t.endswith(suf) and not t.endswith("ss") and len(t) - len(suf) >= 3:
                t = t[: -len(suf)]
                break
    if len(t) > 3 and t.endswith("y"):
        t = t[:-1] + "i"
    if len(t) > 3 and t.endswith("e"):
        t = t[:-1]
    return t


def content_stems(text: str) -> set[str]:
    return {stem(t) for t in TOKEN_RE.findall(text.casefold())
            if t not in STOPWORDS and (t.isdigit() or len(t) >= 2)}


def overlap(query: str, memory_text: str) -> list[str]:
    return sorted(content_stems(query) & content_stems(memory_text))


def osa_distance(a: str, b: str) -> int:
    """Optimal string alignment distance (insertions, deletions, substitutions, adjacent transpositions)."""
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[la][lb]


@dataclass
class Rec:
    kind: str
    topic: str = ""
    role: str = "normal"            # a_target, c_target, b_target, sibling, normal
    pair_id: str | None = None
    signature_id: str | None = None
    identifier: str = ""
    month: tuple[int, int] = (0, 0)
    day: int = 0
    minute_of_day: int = 0
    c3_cell: str | None = None
    fields: dict = field(default_factory=dict)
    content: str = ""
    memory_id: str = ""
    fixed_outcome: str | None = None


def month_text(ym: tuple[int, int]) -> str:
    return f"{calendar.month_name[ym[1]]} {ym[0]}"


def id_family(topic: str, rng: random.Random) -> str:
    if TOPIC_KIND[topic] == "ticket":
        return "TKT"
    if topic in ("plan_upgrade", "plan_downgrade"):
        return "PLN-" + rng.choice(("UNL", "BIZ", "DAT", "VOX", "PRO", "MAX", "SME", "FLX", "NET", "GEO"))
    if topic == "roaming_addon":
        return "ADD-" + rng.choice(("RMG", "GCC", "WLD"))
    if topic == "data_pack":
        return "ADD-" + rng.choice(("DTA", "TOP", "GBX"))
    if topic == "addon_cancel":
        return "ADD-" + rng.choice(("INT", "SMS", "CLD", "VID", "RMG", "DTA"))
    return {"device": "DEV", "billing": "INV", "contact": "NTE"}[TOPIC_KIND[topic]]


def random_id(family: str, rng: random.Random) -> str:
    if family in ("TKT", "DEV", "NTE"):
        return f"{family}-{rng.randint(10000, 99999)}"
    if family == "INV":
        return f"INV-{rng.randint(100000, 999999)}"
    return f"{family}-{rng.randint(10, 99)}"


def sibling_of(ident: str, kind: str, rng: random.Random) -> str | None:
    """A near-collision sibling at OSA distance 1, or None when this identifier cannot have one."""
    prefix, _, digits = ident.rpartition("-")
    if kind == "plan":                     # truncation, as PLN-UNL-50 / PLN-UNL-5
        return f"{prefix}-{digits[:-1]}" if len(digits) == 2 else None
    if kind in ("ticket", "contact"):      # transposition of the last two digits, as TKT-48213 / TKT-48231
        if digits[-1] == digits[-2]:
            return None
        return f"{prefix}-{digits[:-2]}{digits[-1]}{digits[-2]}"
    # device, billing: one digit changed
    pos = len(digits) - 1 - rng.randint(0, 1)
    new = str((int(digits[pos]) + rng.randint(1, 9)) % 10)
    return f"{prefix}-{digits[:pos]}{new}{digits[pos + 1:]}"


def build(seed: int = SEED) -> tuple[list[dict], list[dict], list[dict]]:
    rng = random.Random(seed)
    recs: list[Rec] = []
    by_kind: dict[str, list[Rec]] = {k: [] for k in KIND_COUNTS}

    # 1. Roles. Pairs first (target + sibling), then the non-pair A and C targets, then class B, then the rest.
    pair_no = 0
    for kind, npairs in PAIRS_PER_KIND.items():
        named = [p for p in NAMED_PAIRS if TOPIC_KIND[p[2]] == kind]
        a_left = PAIR_TARGETS_A[kind] - sum(1 for p in named if p[4] == "A")
        c_left = (npairs - PAIR_TARGETS_A[kind]) - sum(1 for p in named if p[4] == "C")
        classes = ["A"] * a_left + ["C"] * c_left
        rng.shuffle(classes)
        for i in range(npairs):
            pair_no += 1
            pid = f"P{pair_no:02d}"
            if i < len(named):
                base, sib, topic, target, cls, _q = named[i]
                t = Rec(kind, topic, "a_target" if cls == "A" else "c_target", pid, identifier=target)
                s = Rec(kind, topic, "sibling", pid, identifier=sib if target == base else base)
            else:
                cls = classes.pop()
                t = Rec(kind, "", "a_target" if cls == "A" else "c_target", pid)
                s = Rec(kind, "", "sibling", pid)
            by_kind[kind] += [t, s]
    for kind in KIND_COUNTS:
        for cls in ("A", "C"):
            have = sum(1 for r in by_kind[kind] if r.role == ("a_target" if cls == "A" else "c_target"))
            for _ in range(TARGETS_PER_KIND[kind] - have):
                by_kind[kind].append(Rec(kind, "", "a_target" if cls == "A" else "c_target"))
    for sid, topic, _d, _q in SIGNATURES:
        by_kind[TOPIC_KIND[topic]].append(Rec(TOPIC_KIND[topic], topic, "b_target", signature_id=sid))
    for kind, n in KIND_COUNTS.items():
        while len(by_kind[kind]) < n:
            by_kind[kind].append(Rec(kind))
        if len(by_kind[kind]) != n:
            raise ValueError(f"{kind}: {len(by_kind[kind])} records for {n} slots")

    # 2. Topics: 20 per topic; a pair shares one topic.
    for kind, rlist in by_kind.items():
        left = {t: PER_TOPIC for t in TOPICS[kind]}
        for r in rlist:
            if r.topic:
                left[r.topic] -= 1
        pairs: dict[str, list[Rec]] = {}
        for r in rlist:
            if r.pair_id:
                pairs.setdefault(r.pair_id, []).append(r)
        for pid in sorted(pairs):
            members = pairs[pid]
            if members[0].topic:
                continue
            choices = [t for t in TOPICS[kind] if left[t] >= 2]
            topic = rng.choice(choices)
            for m in members:
                m.topic = topic
            left[topic] -= 2
        pool = [t for t in TOPICS[kind] for _ in range(left[t])]
        rng.shuffle(pool)
        for r in rlist:
            if not r.topic:
                r.topic = pool.pop()
        if pool or any(v < 0 for v in left.values()):
            raise ValueError(f"{kind}: topic capacity broken")
        recs += rlist

    # 3. Identifiers: unique, one per memory, no accidental near-collision (OSA >= 2 between non-pair ids).
    taken: dict[str, Rec] = {}

    def free(ident: str, allow: set[str]) -> bool:
        if ident in taken:
            return False
        return all(osa_distance(ident, other) >= 2 for other in taken if other not in allow)

    for r in recs:                          # named pairs first
        if r.identifier:
            taken[r.identifier] = r
    named_ids = set(taken)
    for p in NAMED_PAIRS:
        if osa_distance(p[0], p[1]) != 1:
            raise ValueError(f"named pair {p[:2]} is not at distance 1")
    pairs_by_id: dict[str, list[Rec]] = {}
    for r in recs:
        if r.pair_id:
            pairs_by_id.setdefault(r.pair_id, []).append(r)
    for pid in sorted(pairs_by_id):
        t, s = sorted(pairs_by_id[pid], key=lambda x: x.role != "sibling")[::-1]
        if t.identifier:
            continue
        for _attempt in range(10_000):
            base = random_id(id_family(t.topic, rng), rng)
            sib = sibling_of(base, t.kind, rng)
            if sib is None or base == sib:
                continue
            if free(base, set()) and free(sib, {base}):
                if rng.random() < 0.5:
                    base, sib = sib, base
                t.identifier, s.identifier = base, sib
                taken[base], taken[sib] = t, s
                break
        else:
            raise ValueError(f"no identifier pair found for {pid}")
    for r in recs:
        if r.identifier:
            continue
        for _attempt in range(10_000):
            cand = random_id(id_family(r.topic, rng), rng)
            if free(cand, set()):
                r.identifier = cand
                taken[cand] = r
                break
        else:
            raise ValueError(f"no identifier found for a {r.topic} record")
    del named_ids

    # 4. Months: per topic two C3 cells (sizes 2-5), the topic's other memories in other months.
    sizes = [2] * 10 + [3] * 10 + [4] * 10 + [5] * 10
    rng.shuffle(sizes)
    cells = []
    all_topics = [t for k in TOPICS for t in TOPICS[k]]
    for topic in all_topics:
        months = rng.sample(MONTHS, 2)
        for m in months:
            cells.append((topic, m, sizes.pop()))
    rng.shuffle(cells)
    cell_ids = {}
    for i, (topic, m, size) in enumerate(cells, start=1):
        cell_ids[(topic, m)] = (f"C3-{i:02d}", size)
    for topic in all_topics:
        members = [r for r in recs if r.topic == topic]
        rng.shuffle(members)
        cms = [(m, cell_ids[(t, m)]) for (t, m) in cell_ids if t == topic]
        cms.sort()
        pos = 0
        for m, (cid, size) in cms:
            for r in members[pos:pos + size]:
                r.month, r.c3_cell = m, cid
            pos += size
        other_months = [m for m in MONTHS if m not in {c[0] for c in cms}]
        for r in members[pos:]:
            r.month = rng.choice(other_months)
    for r in recs:
        r.day = rng.randint(1, 28)
        r.minute_of_day = rng.randint(7 * 60, 19 * 60)

    # 5. Text.
    sig = {s[0]: s for s in SIGNATURES}
    for r in recs:
        r.fields = {"line": rng.randint(1, 48), "site": rng.choice(SITES), "amt": rng.randint(20, 900),
                    "model": rng.choice(MODELS), "channel": rng.choice(CHANNELS), "role": rng.choice(ROLES),
                    "window": rng.choice(WINDOWS), "country": rng.choice(COUNTRIES)}
        if r.identifier == "TKT-48213":
            r.fixed_outcome = "The charge was refunded in full."
        elif r.identifier == "TKT-48231":
            r.fixed_outcome = "The charge was upheld after review."
    for r in recs:
        date = f"{r.day} {month_text(r.month)}"
        if r.role == "b_target":
            question = sig[r.signature_id][3]
            options = [(h, o) for h in HEADERS[r.topic] for o in OUTCOMES[r.topic]]
            rng.shuffle(options)
            for h, o in options:
                text = " ".join((h, sig[r.signature_id][2], o)).format(id=r.identifier, date=date, **r.fields)
                if len(overlap(question, text)) <= 1:
                    r.content = text
                    break
            else:
                raise ValueError(f"{r.signature_id}: no phrasing keeps the overlap at most 1")
        else:
            h = rng.choice(HEADERS[r.topic])
            d = rng.choice(DETAILS[r.topic])
            o = r.fixed_outcome or rng.choice(OUTCOMES[r.topic])
            r.content = " ".join((h, d, o)).format(id=r.identifier, date=date, **r.fields)

    # 6. Order by event time; memory ids follow that order.
    recs.sort(key=lambda r: (r.month, r.day, r.minute_of_day, r.identifier))
    for i, r in enumerate(recs, start=1):
        r.memory_id = f"biz_m{i:04d}"

    corpus = [{
        "memory_id": r.memory_id, "user_id": USER_ID, "memory_type": "preference" if r.kind == "contact" else "fact",
        "timestamp": f"{r.month[0]:04d}-{r.month[1]:02d}-{r.day:02d}T{r.minute_of_day // 60:02d}:"
                     f"{r.minute_of_day % 60:02d}:00+00:00",
        "content": r.content, "identifier": r.identifier, "kind": r.kind, "topic": r.topic,
        "month": f"{r.month[0]:04d}-{r.month[1]:02d}", "role": r.role, "pair_id": r.pair_id,
        "signature_id": r.signature_id, "c3_cell": r.c3_cell,
    } for r in recs]

    # 7. Queries.
    by_pair: dict[str, list[Rec]] = {}
    for r in recs:
        if r.pair_id:
            by_pair.setdefault(r.pair_id, []).append(r)

    def sibling(r: Rec) -> Rec | None:
        if not r.pair_id:
            return None
        return next(x for x in by_pair[r.pair_id] if x is not r)

    def qrow(qid: str, cls: str, text: str, r: Rec) -> dict:
        sib = sibling(r)
        ov = overlap(text, r.content)
        return {"qid": qid, "qclass": cls, "text": text, "gold_memory_id": r.memory_id,
                "gold_identifier": r.identifier, "kind": r.kind, "topic": r.topic,
                "near_collision": sib is not None, "sibling_identifier": sib.identifier if sib else None,
                "sibling_memory_id": sib.memory_id if sib else None, "signature_id": r.signature_id,
                "overlap_count": len(ov), "overlap_terms": ov}

    queries = []
    a_recs = sorted((r for r in recs if r.role == "a_target"), key=lambda r: r.memory_id)
    rng.shuffle(a_recs)
    for i, r in enumerate(a_recs, start=1):
        queries.append(qrow(f"C1-A-{i:02d}", "A", r.identifier, r))
    b_recs = sorted((r for r in recs if r.role == "b_target"), key=lambda r: r.signature_id)
    for i, r in enumerate(b_recs, start=1):
        queries.append(qrow(f"C1-B-{i:02d}", "B", sig[r.signature_id][3], r))
    c_recs = sorted((r for r in recs if r.role == "c_target"), key=lambda r: r.memory_id)
    rng.shuffle(c_recs)
    named_c = {p[3]: p[5] for p in NAMED_PAIRS if p[4] == "C"}
    for i, r in enumerate(c_recs, start=1):
        if r.identifier in named_c:
            text = named_c[r.identifier]
        else:
            group = C_GROUP.get(r.topic, r.kind)
            text = rng.choice(C_TEMPLATES[group]).format(id=r.identifier)
        queries.append(qrow(f"C1-C-{i:02d}", "C", text, r))

    c3 = []
    for (topic, m), (cid, size) in sorted(cell_ids.items(), key=lambda kv: kv[1][0]):
        gold = sorted(r.memory_id for r in recs if r.topic == topic and r.month == m)
        text = rng.choice(C3_TEMPLATES).format(label=C3_LABEL[topic], month=month_text(m))
        c3.append({"qid": cid, "text": text, "topic": topic, "month": f"{m[0]:04d}-{m[1]:02d}",
                   "gold_memory_ids": gold, "gold_size": len(gold)})

    validate(corpus, queries, c3)
    return corpus, queries, c3


def validate(corpus: list[dict], queries: list[dict], c3: list[dict]) -> None:
    """Raise ValueError when a rule of PLAN section 3, 7.1 or 7.3 is broken."""
    if len(corpus) != 400:
        raise ValueError(f"corpus has {len(corpus)} memories, not 400")
    for kind, n in KIND_COUNTS.items():
        got = sum(1 for m in corpus if m["kind"] == kind)
        if got != n:
            raise ValueError(f"{kind}: {got} memories, not {n}")
    for kind, topics in TOPICS.items():
        for t in topics:
            if sum(1 for m in corpus if m["topic"] == t) != PER_TOPIC:
                raise ValueError(f"topic {t} does not have {PER_TOPIC} memories")
    ids = [m["identifier"] for m in corpus]
    if len(set(ids)) != len(ids):
        raise ValueError("an identifier is used by two memories")
    if len({m["memory_id"] for m in corpus}) != len(corpus):
        raise ValueError("duplicate memory ids")
    for m in corpus:
        found = ID_RE.findall(m["content"])
        shapes = ID_SHAPE_RE.findall(m["content"])
        if found != [m["identifier"]] or len(shapes) != 1:
            raise ValueError(f"{m['memory_id']}: must hold exactly its own identifier, found {found} {shapes}")
        if m["user_id"] != USER_ID:
            raise ValueError("wrong user id")
    # every identifier occurs in exactly one memory (token match, so PLN-UNL-5 is not found inside PLN-UNL-50)
    for ident in ids:
        pat = re.compile(r"(?<![A-Za-z0-9-])" + re.escape(ident) + r"(?![A-Za-z0-9-])")
        if sum(1 for m in corpus if pat.search(m["content"])) != 1:
            raise ValueError(f"{ident} does not occur in exactly one memory")
    pairs: dict[str, list[dict]] = {}
    for m in corpus:
        if m["pair_id"]:
            pairs.setdefault(m["pair_id"], []).append(m)
    if len(pairs) != 30 or any(len(v) != 2 for v in pairs.values()):
        raise ValueError("need 30 near-collision pairs of two memories")
    pair_sets = {frozenset(x["identifier"] for x in v) for v in pairs.values()}
    for v in pairs.values():
        if osa_distance(v[0]["identifier"], v[1]["identifier"]) != 1:
            raise ValueError(f"pair {v[0]['pair_id']} is not at distance 1")
        if v[0]["kind"] != v[1]["kind"] or v[0]["topic"] != v[1]["topic"]:
            raise ValueError(f"pair {v[0]['pair_id']} mixes kinds or topics")
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if osa_distance(ids[i], ids[j]) < 2 and frozenset((ids[i], ids[j])) not in pair_sets:
                raise ValueError(f"accidental near-collision {ids[i]} / {ids[j]}")
    for p in NAMED_PAIRS:
        if frozenset(p[:2]) not in pair_sets:
            raise ValueError(f"named pair {p[:2]} missing")

    by_mid = {m["memory_id"]: m for m in corpus}
    if len(queries) != 120:
        raise ValueError("need 120 C1 queries")
    for cls in "ABC":
        qs = [q for q in queries if q["qclass"] == cls]
        if len(qs) != 40:
            raise ValueError(f"class {cls} has {len(qs)} queries")
        if cls in "AC" and sum(q["near_collision"] for q in qs) != 15:
            raise ValueError(f"class {cls} needs 15 near-collision targets")
    if len({q["gold_memory_id"] for q in queries}) != 120:
        raise ValueError("the 120 gold memories must be distinct")
    for q in queries:
        g = by_mid[q["gold_memory_id"]]
        if g["identifier"] != q["gold_identifier"]:
            raise ValueError(f"{q['qid']}: gold identifier mismatch")
        in_q = ID_RE.findall(q["text"])
        if q["qclass"] == "A" and q["text"] != g["identifier"]:
            raise ValueError(f"{q['qid']}: class A must be the bare identifier")
        if q["qclass"] == "C" and (in_q != [g["identifier"]] or q["text"] == g["identifier"]):
            raise ValueError(f"{q['qid']}: class C must be a question holding exactly the gold identifier")
        if q["qclass"] == "B":
            if in_q or ID_SHAPE_RE.search(q["text"]) or MONTH_WORD_RE.search(q["text"]):
                raise ValueError(f"{q['qid']}: class B must hold no identifier, month or year")
            if q["overlap_count"] > 1:
                raise ValueError(f"{q['qid']}: overlap {q['overlap_terms']} > 1")
            if sum(1 for m in corpus if m["signature_id"] == q["signature_id"]) != 1:
                raise ValueError(f"{q['qid']}: its signature is not unique in the corpus")
        if q["overlap_terms"] != overlap(q["text"], g["content"]):
            raise ValueError(f"{q['qid']}: stored overlap is stale")
        if q["near_collision"]:
            sib = by_mid[q["sibling_memory_id"]]
            if sib["pair_id"] != g["pair_id"] or sib["identifier"] != q["sibling_identifier"]:
                raise ValueError(f"{q['qid']}: sibling mismatch")
    if not any(q["text"] == "why was TKT-48213 refunded?" for q in queries):
        raise ValueError("PLAN 7.1's class C example is missing")

    if len(c3) != 40:
        raise ValueError("need 40 C3 queries")
    for q in c3:
        gold = sorted(m["memory_id"] for m in corpus if m["topic"] == q["topic"] and m["month"] == q["month"])
        if gold != q["gold_memory_ids"] or not (2 <= len(gold) <= 5) or q["gold_size"] != len(gold):
            raise ValueError(f"{q['qid']}: gold set is not topic x month or not 2-5")
        if any(by_mid[g]["c3_cell"] != q["qid"] for g in gold):
            raise ValueError(f"{q['qid']}: cell labels disagree")
        y, mo = int(q["month"][:4]), int(q["month"][5:])
        mt = f"{calendar.month_name[mo]} {y}"
        if mt not in q["text"]:
            raise ValueError(f"{q['qid']}: month missing from the question")
        for g in gold:
            if mt not in by_mid[g]["content"]:
                raise ValueError(f"{q['qid']}: gold {g} does not state its month")
    if len({(q["topic"], q["month"]) for q in c3}) != 40:
        raise ValueError("C3 cells are not distinct")
    for m in corpus:
        months = MONTH_WORD_RE.findall(m["content"])
        names = [x for x in months if not x.isdigit()]
        if len(names) != 1:
            raise ValueError(f"{m['memory_id']}: must name exactly one month")


def dump_jsonl(rows: list[dict]) -> bytes:
    return "".join(json.dumps(r, sort_keys=True, ensure_ascii=True) + "\n" for r in rows).encode("ascii")


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix="." + path.name + ".")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args(argv)
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)sZ %(levelname)s %(name)s %(message)s")
    corpus, queries, c3 = build(SEED)
    blobs = dict(zip(FILES, (dump_jsonl(corpus), dump_jsonl(queries), dump_jsonl(c3))))
    if args.check:
        bad = [f for f, b in blobs.items() if not (args.out / f).is_file() or (args.out / f).read_bytes() != b]
        if bad:
            LOG.error("check failed: %s differ from a fresh generation", ", ".join(bad))
            return 1
        LOG.info("check ok: %s identical to a fresh generation (seed %d)", ", ".join(FILES), SEED)
        return 0
    for f, b in blobs.items():
        atomic_write(args.out / f, b)
        LOG.info("wrote %s (%d bytes)", f, len(b))
    LOG.info("corpus %d memories, C1 %d queries, C3 %d queries, seed %d", len(corpus), len(queries), len(c3), SEED)
    return 0


if __name__ == "__main__":
    sys.exit(main())
