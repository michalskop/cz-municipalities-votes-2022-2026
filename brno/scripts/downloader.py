"""Downloads and transforms data to standard format."""

import csv
import requests
import pandas as pd

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

# rename groups
def group_to_group(group):
  if group == 'ANO 2011':
    return 'ANO'
  elif group == 'ČSSD VAŠI STAROSTOVÉ':
    return 'ČSSD'
  elif group == 'Lidovci a Starostové':
    return 'KDU-ČSL a STAN'
  else:
    return group
  
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
          'group_id': group_to_group(group['name']),
          'option': option_to_option(voter['text'])
        }
        votes.append(vote)

# remove duplicates
vote_events_df = pd.DataFrame(vote_events)
votes_df = pd.DataFrame(votes)
vote_events_df.drop_duplicates(subset=['id'], inplace=True)
votes_df.drop_duplicates(subset=['vote_event_id', 'voter_id'], inplace=True)

# creeate voters
voters_df = votes_df.loc[:, ['voter_id', 'group_id']].drop_duplicates().drop_duplicates(subset=['voter_id'])
voters_df['name'] = voters_df['voter_id']
voters_df.rename(columns={'voter_id': 'id', 'group_id': 'party'}, inplace=True)

# save data
vote_events_df.to_csv(localpath + "data/vote_events.csv", index=False)
votes_df.to_csv(localpath + "data/votes.csv", index=False)
voters_df.to_csv(localpath + "data/voters.csv", index=False)