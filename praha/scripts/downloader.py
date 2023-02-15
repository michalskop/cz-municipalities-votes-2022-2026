"""Downloads and transforms data to standard format."""

import datetime
from lxml import html
import math
import pandas as pd
import re
import requests

localpath = "praha/"

# remove titles
academic_titles = ['Mgr.', 'Ing.', 'M', 'DDS', 'DVM', 'PharmD']
def remove_titles(name):
  comma_index = name.find(',')
  if comma_index != -1:
    name = name[0:comma_index]
  dot_index = name.rfind('.')
  if dot_index != -1:
    name = name[dot_index + 1:].strip()
  return name

# get group from group (party) name
def group_from_group(group):
  braces_index = group.find('(')
  if braces_index != -1:
    group = group[0:braces_index].strip()
  return group

# get party from group (party) name
def party_from_group(group):
  braces_index = group.find('(')
  if braces_index != -1:
    group = group[braces_index + 1:-1].strip(')')
  return group

# rename groups / parties
def group_to_group(group):
  if group == 'ANO 2011':
    return 'ANO'
  elif group == 'SPOLU pro Prahu':
    return 'SPOLU'
  elif group == 'Zastupitelský klub SPD':
    return 'SPD'
  else:
    return group

# get current voters
url = "https://www.praha.eu/jnp/cz/o_meste/primator_a_volene_organy/zastupitelstvo/seznam_zastupitelu/index.html?size=100"
r = requests.get(url)
if r.status_code == 200:
  domtree = html.fromstring(r.text)
  trs = domtree.xpath('//tbody/tr')

  # for each person
  voters_arr = []
  for tr in trs:
    person = {}
    tds = tr.xpath('td')
    person['id'] = int(re.search('memberId=(\d{1,})',tds[0].xpath('a/@href')[0]).group(1).strip())
    person['name'] = remove_titles(tds[0].xpath('a')[0].text.strip())
    try:
      person['party'] = group_to_group(party_from_group(tds[1].text.strip()))
      person['group'] = group_to_group(group_from_group(tds[1].text.strip()))
    except:
      person['party'] = ''
      person['group'] = ''
    person['email'] = tds[2].xpath('a')[0].text.strip()
    voters_arr.append(person)
else:
  raise(Exception)

# update voters
voters = pd.read_csv(localpath + "data/voters.csv")
current_voters = pd.DataFrame(voters_arr)

voters = voters.merge(current_voters, on='id', how='outer', suffixes=('_old', '_new'))
voters['name'] = voters['name_new'].fillna(voters['name_old'])
voters['party'] = voters['party_new'].fillna(voters['party_old'])
voters['group'] = voters['group_new'].fillna(voters['group_old'])
voters['email'] = voters['email_new'].fillna(voters['email_old'])

voters = voters.drop(['name_old', 'name_new', 'party_old', 'party_new', 'email_old', 'email_new', 'group_old', 'group_new'], axis=1)

voters.to_csv(localpath + "data/voters.csv", index=False)


# vote events
# period id
terms = {
  # '1998-2002': 18280,
  # '2002-2006': 18281,
  # '2006-2010': 18282,
  # '2010-2014': 18284,
  # '2014-2018': 29783,
  # '2018-2022': 33394,
  '2022-2026': 36525
}
period_id = 36525

# get number of pages
url0 = 'https://www.praha.eu/jnp/cz/o_meste/primator_a_volene_organy/zastupitelstvo/vysledky_hlasovani/index.html?size=5&periodId=' + str(period_id) + '&resolutionNumber=&printNumber=&s=1&meeting=&start=0'
r = requests.get(url0)
domtree = html.fromstring(r.text)
tcount = domtree.xpath('//div[@class="pg-count"]/strong')[0].text.strip()
npages = math.ceil(int(tcount) / 500)  # 500 is max per page
n = 0

# recode results  
def result_to_result(result):
  if result == 'Ano':
    return 'pass'
  elif result == 'Ne':
    return 'fail'
  else:
    return 'unknown'

# get vote events
vote_events = []
# for each page in pagination
for page in range(0,npages):
  url = 'https://www.praha.eu/jnp/cz/o_meste/primator_a_volene_organy/zastupitelstvo/vysledky_hlasovani/index.html?size=500&periodId=' + str(period_id) + '&resolutionNumber=&printNumber=&s=1&meeting=&start=' + str(page*500)
  rr = requests.get(url)
  if rr.status_code == 200:
    domtree = html.fromstring(rr.text)
    trs = domtree.xpath('//tbody/tr')
    # for each vote event
    for tr in trs:
      ve = {}
      tds = tr.xpath('td')
      ve['id']  = re.search('votingId=(\d{1,})',tds[4].xpath('a/@href')[0]).group(1).strip()
      ve['date'] = datetime.datetime.strptime(tds[1].text,"%d.%m.%Y").strftime("%Y-%m-%d")
      ve['motion:result'] = result_to_result(tds[4].xpath('a')[0].text.strip())
      try:
        ve['motion:number'] = tds[0].text.strip()
      except:
        ve['motion:number'] = ''
      try:
        ve['motion:document'] = tds[2].text.strip()
      except:
        ve['motion:document'] = ''
      try:
        ve['motion:title'] = tds[3].text.strip()
      except:
        ve['motion:title'] = ''
      ve['sources:link:url'] = 'https://www.praha.eu' + tds[4].xpath('a/@href')[0].strip()
     
      ve['identifier'] = ve['id']
      vote_events.append(ve)
  else:
    raise(Exception)

# update vote events
vote_events = pd.DataFrame(vote_events)
vote_events.to_csv(localpath + "data/vote_events.csv", index=False)


# votes

# recode options
def option_to_option(option):
  if option == 'pro':
    return 'yes'
  elif option == 'proti':
    return 'no'
  elif option == 'zdržel se':
    return 'abstain'
  elif option == 'chyběl':
    return 'absent'
  elif option == 'nehlasoval':
    return 'not voting'
  else:
    return 'unknown'

# load votes
votes = pd.read_csv(localpath + "data/votes.csv")
old_votes_ids = votes['vote_event_id'].unique()

# extract votes
for i,ve in vote_events.iterrows():
  if int(ve['id']) not in old_votes_ids:
    # get vote event
    url = ve['sources:link:url']
    r = requests.get(url)
    if r.status_code == 200:
      domtree = html.fromstring(r.text)
      trs = domtree.xpath('//tbody/tr')
      # for each vote
      for tr in trs:
        tds = tr.xpath('td')
        vote = {}
        vote['vote_event_id'] = ve['id']
        vote['voter_id'] = re.search('memberId=(\d{1,})',tds[0].xpath('a/@href')[0]).group(1).strip()
        vote['voter_name'] = remove_titles(tds[0].xpath('a')[0].text.strip())
        vote['option'] = option_to_option(tds[1].text.strip())
        # group and party
        vote['group_id'] = voters[voters['id'] == int(vote['voter_id'])]['group'].values[0]
        vote['party_id'] = voters[voters['id'] == int(vote['voter_id'])]['party'].values[0]
        # add vote
        votes = pd.concat([votes, pd.DataFrame([vote])], ignore_index=True)
    else:
      raise(Exception)

# update votes
votes.to_csv(localpath + "data/votes.csv", index=False)