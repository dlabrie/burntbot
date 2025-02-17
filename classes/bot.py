import threading
import time
import traceback

import globals

from api.transactions import get_transactions
from api.exception import ClientException
from utilities.transaction_parser import populate_history, get_swaps
from utilities.datetime import get_swap_datetime, string_to_datetime
from utilities.persistence import upsert_persistence
from utilities.log import log
from utilities.swap import swap

class SwapBot(threading.Thread):
	def __init__(self):
		threading.Thread.__init__(self, daemon = True)

		self.restarts = 0
		self.last_restart = time.time()

	def init_history(self) -> float:
		body = {
			'filterParams': {'type': 'peer'},
			'pagination': {
				'descending': True,
				'page': 1,
				'rowsPerPage': 2000
			}
		}

		swapping_begin_datetime = get_swap_datetime()
		last_time = int(time.time())
		rate_limit_timeout = 0

		while (1):
			log(f'Fetching page {body["pagination"]["page"]} of transactions')

			(response, headers) = get_transactions(body)
			data = response['data']

			if (len(data) == 0): break

			populate_history(data)
			
			rate_limit_timeout = headers['Retry-After']

			# check if we need to stop fetching history
			log(f'{data[-1]["timestamp"]} vs {swapping_begin_datetime}', True)
			if (string_to_datetime(data[-1]['timestamp']) < swapping_begin_datetime):
				break

			body['pagination']['page'] = body['pagination']['page'] + 1

			# prevent rate limit by ensuring we maintain window between fetches
			time_left = 5 - (int(time.time()) - last_time)
			if (time_left > 0):
				time.sleep(time_left)

			last_time = int(time.time())

		return rate_limit_timeout

	def run(self):
		while (1):
			try:
				# init hash table of transactions
				log('Initializing swap history today')
				wait_time = self.init_history()

				# adjust swaps by our blacklist function
				for shaketag, amount in globals.bot_blacklist.items():
					pass

				# iterate through transactions and apply adjustments and pay back those need swaps
				# instead of adjust, then swap, do it together, saves CPU time
				# additionally, check for donation to @cfcc for a "pizza paddle"
				for _, history in globals.bot_history.items():
					shaketag = history.get_shaketag()

					if (shaketag in globals.bot_blacklist):
						history.adjust_swap(globals.bot_blacklist[shaketag])

					amount = history.get_swap()

					if (amount != 0.): log(f'{shaketag}\twith\t{amount}\t{history.get_timestamp()}', True)

					if (amount > 0.):
						log(f'Late send ${amount} to {shaketag} ({history.get_timestamp()})')
						swap(shaketag, amount)

					if (shaketag == '@cfcc'):
						with open('./static/pizza.txt') as file:
							globals.waitlist_paddles.append({'icon': file.read()})

				# this isnt 100% accurate since there maybe late send backs
				log(f'Waiting {wait_time} seconds for rate limit expiry')
				time.sleep(float(wait_time))

				log('Bot ready')

				# start polling
				while (1):
					(response_json, headers) = get_transactions({'filterParams': {'currencies': ['CAD']}})
					swap_list = get_swaps(response_json['data'])

					for userid in swap_list:
						user_details = globals.bot_history[userid]
						
						shaketag = user_details.get_shaketag()
						amount = user_details.get_swap()
						
						swap(shaketag, amount)
						
					time.sleep(globals.bot_poll_rate)
			except ClientException:
				log('Bot died due to HTTP client error, stopping')
				upsert_persistence({'token': ''})

				raise SystemExit(0)
			except Exception as e:
				log(f'Crashed due to: {e}')
				log(traceback.format_exc())

				globals.bot_history = {}

				time_now = time.time()

				# if bot last restart was > 10 minutes ago, reset counter
				if (self.last_restart + (60 * 10) < time_now):
					self.restarts = 0

				if (self.restarts > 5):
					log('Bot died from too many deaths, stopping')
					
					raise SystemExit(0)
				else:
					log('Bot died due to uncaught exception, restarting after 60 seconds')

					self.restarts = self.restarts + 1
					self.last_restart = time_now

					time.sleep(60)
