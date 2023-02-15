"""Downloads and transforms data to standard format."""

import csv
import requests
# import pandas as pd

start_date = "2022-10-01"
localpath = "brno/"

# download data
url = "https://kod.brno.cz/zastupitelstvo/"
source_data = requests.get(url).json()

# results to popolo standard
def result_to_result(result):
  if result == "Přijato":
    return "pass"
  elif result == "Nepřijato":
    return "fail"
  else:
    return "unknown"

# option to popolo standard
def option_to_option(option):
  if (option == "Ano") or (option == "Ano (VK)"):
    return "yes"
  elif (option == "Ne") or (option == "Ne (VK)"):
    return "no"
  elif (option == "Zdržel se") or (option == "Zdržel se (VK)"):
    return "abstain"
  elif option == "nepřít.":
    return 'absent'
  elif (option == "Nehlasoval") or (option == "Nehlasoval (VK)"):
    return 'not voting'
  else:
    return "unknown"

# transform data
vote_events = []
votes = []

for row in source_data['data']:
  if (row['datetime'] > start_date) and (row['details']['present'] > 10):  # skip empty/error votes
    vote_event_id = row['datetime'][0:10] + "-" + str(row['number']).zfill(3)
    # vote events
    vote_event = {
      'id': vote_event_id,
      'date': row['datetime'],
      'motion:title': row['subject'],
      'motion:result': result_to_result(row['result']),
      'code': row['code'],
      'number': row['number'],
      'counts:yes': row['details']['yes'],
      'counts:no': row['details']['no'],
      'counts:abstain': row['details']['abstained'],
    }
    vote_events.append(vote_event)

    # votes
    for group in row['parties']:
      for voter in group['votes']:
        vote = {
          'vote_event_id': vote_event_id,
          'voter_id': voter['voter'],
          'group_id': group['name'],
          'option': option_to_option(voter['text'])
        }
        votes.append(vote)

# save data
with open(localpath + 'data/vote_events.csv', 'w') as f:
  writer = csv.DictWriter(f, fieldnames=vote_events[0].keys())
  writer.writeheader()
  writer.writerows(vote_events)

with open(localpath + 'data/votes.csv', 'w') as f:
  writer = csv.DictWriter(f, fieldnames=votes[0].keys())
  writer.writeheader()
  writer.writerows(votes)