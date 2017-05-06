# -*- coding: utf-8 -*-
"""
Created on Wed May  3 21:09:01 2017

@author: ACKWinDesk

stravascrape.py:

Design:
    Using selenium because this year's leaderboards requires a button press?
    Cummulative time over segments to smooth them over
        This unfortunately doesn't put much weight on short segments

Possible Issues:
    Algorithm doesn't really make sense if early in the year

Future changes:
    Switch to PhantomJS instead of Chrome
"""
import time
import re
import os
from bs4 import BeautifulSoup
from selenium import webdriver
import selenium.common.exceptions
from numpy import mean
import sqlite3

direc = os.path.dirname(__file__)
chromedriver = direc + '/chromedriver'
os.environ["webdriver.chrome.driver"] = chromedriver


def convert_time_to_seconds(time_string):
    """Quick funtion to convert strava string times to seconds"""
    if time_string[-1] == 's':
        return int(time_string[:-1])
    else:
        denominations = [int(t) for t in time_string.split(':')]
        converts = [60**i for i in reversed(range(len(denominations)))]
        return sum([c*d for c, d in zip(converts, denominations)])


class StravaScraper(object):
    STRAVA = "http://strava.com"
    TIME_OUT = 1  # seconds to sleep between calls (just in case)
    CURRENT_YEAR = 2017
    MY_ATHLETE_ID = #<Enter your id here as a string>
    EMAIL = #<Your email>
    PASSWORD = #<Your password>
    SQL_BASE = 'strava_scraper.sqlite'    # name of the sqlite database file

    def __init__(self):
        """Initialize selenium webdriver"""
        self.driver = webdriver.Chrome(chromedriver)

    def connect_to_database(self, database=None):
        """Connect to sqlite database for storing leaderboards"""
        try:
            self.disconnect_from_database()
        except:
            pass
        if database is None:
            database = self.SQL_BASE
        self.database = database
        self.conn = sqlite3.connect(self.database)
        self.c = self.conn.cursor()

    def disconnect_from_database(self):
        self.conn.close()

    def login(self, email=None, password=None):
        """Navigate to Strava login and enter credentials"""
        if email is None:
            email = self.EMAIL
        if password is None:
            password = self.PASSWORD
        self.driver.get(self.STRAVA + "/login")
        self.driver.find_element_by_id("email").send_keys(email)
        self.driver.find_element_by_id("password").send_keys(password)
        self.driver.find_element_by_id("login-button").click()

    def get_rides(self):
        """Navigate to activites page and scrape rides for this year.
        Requires login."""
        self.driver.get(self.STRAVA + "/athlete/training")
        # Switch form to only bike rides
        self.driver.find_element_by_xpath("//select[@id='activity_type']"
                                          "/option[@value='Ride']").click()
        self.all_rides = []
        while True:
            time.sleep(self.TIME_OUT)
            page = BeautifulSoup(self.driver.page_source)
            dates = page.find_all("td", class_="view-col col-date")
            date_check = [date.get_text()[-4:] != str(self.CURRENT_YEAR)
                          for date in dates]
            links_on_page = page.find_all("td", class_="view-col col-title")
            self.all_rides.extend([a_link.a["href"] for date, a_link in
                                   zip(date_check, links_on_page) if not date])
            if any(date_check):
                break   # found a date not in CURRENT_YEAR
            try:
                self.driver.find_element_by_class_name("next_page").click()
            except selenium.common.exceptions.NoSuchElementException:
                break

    def get_segments(self):
        """Get all unique segments by scraping from each ride page.
        Requires login."""
        self.segments = set()
        # Have to use regular expression search because for some weird reason
        # segment ids are not in html tags but are burried in javascript
        regex1 = re.compile('"segment_id":\d+,"starr')
        regex2 = re.compile('\d+')
        for ride in self.all_rides:
            time.sleep(self.TIME_OUT)
            self.driver.get(ride)
            text = re.findall(regex1, self.driver.page_source)
            self.segments = self.segments | \
                {re.findall(regex2, t)[0] for t in text}
        self.save_segments()

    def save_segments(self):
        """Save segment ids in sqlite table. Requires login."""
        self.c.execute("CREATE TABLE Segments (segment_id TEXT PRIMARY KEY)")
        for segment in self.segments:
            self.c.execute("INSERT INTO Segments VALUES (?)", (segment,))
            self.conn.commit()

    def load_segments(self):
        """Load segments saved in database"""
        sids = self.c.execute("SELECT * FROM Segments").fetchall()
        self.segments = [s[0] for s in sids]

    def get_leaderboards(self, segments=None):
        """Get all current year leaderboards scraping from each segment page.
        Requires login"""
        if segments is None:
            self.load_segments()
            segments = self.segments
        for segment in segments:
            segment_url = self.STRAVA + '/segments/' + segment
            self.driver.get(segment_url)
            time.sleep(self.TIME_OUT)
            try:
                self.driver.find_element_by_link_text("This Year").click()
            # Hazardous segments don't show leaderboard so skip em
            except selenium.common.exceptions.NoSuchElementException:
                continue
            # Find how many places are in leaderboard (to know when to stop)
            partial_board = BeautifulSoup(self.driver.page_source)
            total_places = int(partial_board.find_all("td",
                               class_="standing text-nowrap")[0]
                               .get_text().split('/ ')[-1])
            athlete_ids = []
            ranks = []
            times = []
            try:
                # If segment already exists in table this will throw exception
                self.c.execute("CREATE TABLE " + "S" + segment +
                               " (athlete_id TEXT PRIMARY KEY, "
                               "percentile REAL, time REAL)")
                while True:
                    time.sleep(self.TIME_OUT)
                    partial_board = BeautifulSoup(self.driver.page_source)

                    # Get Athletes
                    athletes = partial_board.find_all("td", class_="athlete")
                    athlete_ids.extend([dude.a["href"].split('/')[-1]
                                        for dude in athletes])
                    # Get times
                    times_text = partial_board.find_all("td",
                                                        class_="last-child")
                    times.extend([convert_time_to_seconds(t.get_text())
                                  for t in times_text])
                    # Get Ranks
                    ranks_html = partial_board.find_all("td",
                                                        class_="text-center")
                    for r in ranks_html:
                        try:
                            ranks.append(int(r.get_text()))
                        # KOMs have pictures instead of ranks
                        except ValueError:
                            ranks.append(1)
                    # Got all ranks, so break
                    if any([r == total_places for r in ranks]):
                        break
                    # If we are stuck in a loop for check if KOM is in twice
                    if athlete_ids.count(athlete_ids[0]) > 1:
                        raise StopIteration
                    try:  # sometimes shared final rank != total_places
                        self.driver.find_element_by_link_text("→").click()
                    except selenium.common.exceptions.NoSuchElementException:
                        break
            except (StopIteration, AssertionError):
                time.sleep(self.TIME_OUT)
                # Wasn't switching pages or didnt get to current year
                continue  # just skip this segment

            # convert ranks to percentiles
            percentiles = [(r-1)/total_places for r in ranks]
            # Make table for this leaderboard
            try:
                self.c.execute("CREATE TABLE " + "S" + segment +
                               " (athlete_id TEXT PRIMARY KEY, "
                               "percentile REAL, time REAL)")
                for row in zip(athlete_ids, percentiles, times):
                    self.c.execute("INSERT INTO " + "S" + segment +
                                   " VALUES (?,?,?)", row)
                self.conn.commit()
            except:
                continue  # anything goes wrong with this table, move on

    def kill_driver(self):
        """Logout and kill driver"""
        # Logout hasn't been implemented yet
        self.driver.quit()

    def find_similar_cyclists(self, segments=None):
        """Use current year leaderboards of current year segments ridden by me
        to find "local" riders in geography and time who have similar score"""
        # Not sure how do to this in SQL so I'll use python
        # First get all unique athlete ids among all segments
        # Could have done this when scraping...
        if segments is None:
            self.load_segments()
            segments = self.segments
        athlete_ids_set = set()
        for segment in self.segments:
            try:
                aids = self.c.execute("SELECT athlete_id FROM S" +
                                      segment).fetchall()
                athlete_ids_set = athlete_ids_set | {a[0] for a in aids}
            except:
                # missing table s from data base...
                pass

        # Each athelete id has a dict that accumulates their scores
        # Two scores: mean of leaderboard percentiles and
        # cumulative segment time
        athlete_stats = {ath: {'percentiles': [], 'cum_times': [0, 0]}
                         for ath in athlete_ids_set}
        for segment in self.segments:
            try:
                my_row = self.c.execute("SELECT * FROM S" +
                                        segment + " WHERE athlete_id=" +
                                        self.MY_ATHLETE_ID).fetchall()[0]
                table = self.c.execute("SELECT * FROM S" +
                                        segment).fetchall()
                for t in table:
                    athlete_stats[t[0]]['percentiles'].append(t[1])
                    athlete_stats[t[0]]['cum_times'][0] += t[2]
                    athlete_stats[t[0]]['cum_times'][1] += my_row[2]
            except:
                # missing table s from data base...
                pass

        # Find similarly scored riders
        my_mean_score = mean(athlete_stats[self.MY_ATHLETE_ID]['percentiles'])
        count_thresh = max([25, len(self.segments)/10])
        low_thresh = max([0, my_mean_score-5])
        high_thresh = min([100, my_mean_score+10])
        slow_scale = .9  # 10% slower
        fast_scale = 1.05  # 5% faster
        new_friend_ids = [ath for ath in athlete_stats
           if len(athlete_stats[ath]['percentiles']) > count_thresh and
           low_thresh <= mean(athlete_stats[ath]['percentiles']) <= high_thresh
           and slow_scale*athlete_stats[ath]['cum_times'][1] <=
           athlete_stats[ath]['cum_times'][0] <=
           fast_scale*athlete_stats[ath]['cum_times'][1]]
        return new_friend_ids

    def follow(self, athletes):
        for ath in athletes:
            # make sure athlete isn't me
            if ath == self.MY_ATHLETE_ID:
                continue
            time.sleep(self.TIME_OUT)
            profile_url = self.STRAVA + "/athletes/" + ath
            self.driver.get(profile_url)
            self.driver.find_element_by_class_name("follow").click()

    def add_athletes_to_kudos_list(self, athletes=None, database=None):
        """Add athletes to table that controls which athletes
        to give kudos to. athletes is a list of athlete id strings"""
        self.connect_to_database(database)
        # Make the table if it doesn't exist
        try:
            self.c.execute("CREATE TABLE kudos_list "
                           "(athlete_id TEXT PRIMARY KEY)")
        except:
            pass
        try:
            for ath in athletes:
                self.c.execute("INSERT INTO kudos_list VALUES (?)", (ath,))
            self.conn.commit()
        except:
            pass

    def give_kudos(self, athletes=None, database=None):
        """Give kudos for every activity this week to a list of athletes.
        Requires login"""
        if athletes is None:
            self.connect_to_database(database)
            athletes = self.c.execute("SELECT athlete_id "
                                      "FROM kudos_list").fetchall()
            athletes = [ath[0] for ath in athletes]
        for ath in athletes:
            profile_url = self.STRAVA + "/athletes/" + ath
            self.driver.get(profile_url)
            while True:
                time.sleep(self.TIME_OUT)
                try:
                    self.driver.find_element_by_class_name("js-add-kudo"). click()
                except:
                    break


def main():
    ss = StravaScraper()
    ss.connect_to_database('strava_scraper.sqlite')
    ss.login()
    ss.get_rides()
    ss.get_segments()
    ss.get_leaderboards()
    friends = ss.find_similar_cyclists()
    ss.follow(friends)
    ss.give_kudos()

if __name__ == "__main__":
    main()
