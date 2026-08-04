# Brno

## Charts
- https://public.flourish.studio/visualisation/12763909/
- https://public.flourish.studio/visualisation/12764879/
- https://public.flourish.studio/visualisation/12765133/
- https://public.flourish.studio/visualisation/14119828/

## Source
https://data.brno.cz/documents/mestobrno::hlasov%C3%A1n%C3%AD-zastupitelstva-city-council-voting-data/explore 

## Alternative App:
https://data.brno.cz/apps/hlasov%C3%A1n%C3%AD-brn%C4%9Bnsk%C3%A9ho-zastupitelstva/explore

## Analysis flow
- `downloader.py` - downloads data
- `attendancer.py` - calculates attendance
- `rebeliter.py` - calculates rebel votes

- `wpca.py` - calculates WPCA
- `wpca-charter.py` - prepares data for the WPCA chart