//+------------------------------------------------------------------+
//|                                           CalendarExporter.mq5   |
//|                         MT5 Trading Workstation - SPEC C3.9      |
//|                                                                  |
//| The MetaTrader5 Python API cannot read the MT5 economic calendar,|
//| so this EA runs INSIDE the user's terminal and exports the       |
//| calendar to a CSV the Workstation re-reads every few minutes.    |
//|                                                                  |
//| Output: <Common Files>\Files\mt5_workstation_calendar.csv        |
//|                                                                  |
//| CSV format (one event per line, UTF-8, first row = header):      |
//|   time_utc,currency,title,impact,actual,forecast,previous        |
//|   2024-03-12T13:30:00Z,USD,"CPI y/y",high,3.2,3.1,3.1            |
//|                                                                  |
//| Times are written in REAL UTC (converted from the terminal's     |
//| server clock via TimeGMT) so the app's risk windows line up      |
//| regardless of the broker's timezone. The file is written to a    |
//| temp name and atomically moved into place, and the calendar is   |
//| queried BEFORE the old file is touched, so a transient failure   |
//| never destroys the previous good export.                         |
//|                                                                  |
//| Install: MetaEditor -> compile -> attach to ANY chart. Read-only |
//| EA: no trading permission is required.                           |
//+------------------------------------------------------------------+
#property copyright "MT5 Trading Workstation"
#property version   "1.10"
#property strict

//--- settings
input string InpFileName   = "mt5_workstation_calendar.csv"; // output file (Common\Files)
input int    InpDaysBack   = 7;                              // also export past N days
input int    InpDaysAhead  = 14;                             // export next N days
input int    InpRefreshMin = 1;                              // rewrite interval (minutes)

datetime g_lastWrite = 0;

//+------------------------------------------------------------------+
//| Map MQL5 calendar importance to the app's impact strings.        |
//+------------------------------------------------------------------+
string ImportanceToString(ENUM_CALENDAR_EVENT_IMPORTANCE importance)
  {
   switch(importance)
     {
      case CALENDAR_IMPORTANCE_HIGH:
         return "high";
      case CALENDAR_IMPORTANCE_MODERATE:
         return "medium";
      default:
         return "low";
     }
  }

//+------------------------------------------------------------------+
//| Real-UTC ISO string: "2024-03-12T13:30:00Z".                     |
//| Calendar times follow the terminal's SERVER clock; subtracting   |
//| (TimeTradeServer - TimeGMT) converts them to true UTC so the     |
//| app's news risk windows fire at the right moment (previously     |
//| server time was labelled Z/UTC and the risk filter was off by    |
//| the broker's UTC offset, typically +2/+3 h).                     |
//+------------------------------------------------------------------+
string FormatIsoUtc(datetime server_time)
  {
   datetime utc   = server_time - (TimeTradeServer() - TimeGMT());
   string   day   = TimeToString(utc, TIME_DATE);     // "2024.03.12"
   string   time  = TimeToString(utc, TIME_MINUTES);  // "13:30"
   StringReplace(day, ".", "-");
   return day + "T" + time + ":00Z";
  }

//+------------------------------------------------------------------+
//| Format a calendar value or "" when not set.                      |
//+------------------------------------------------------------------+
string ValueToString(const long value)
  {
   if(value == CALENDAR_VALUE_NONE)
      return "";
   return DoubleToString((double)value / 1e6, 3);
  }

//+------------------------------------------------------------------+
//| CSV-quote one field: wrap in quotes and double embedded quotes.  |
//| Event titles are localized and may contain commas or quotes.     |
//+------------------------------------------------------------------+
string CsvQuote(const string text)
  {
   string out = text;
   StringReplace(out, "\"", "\"\"");
   return "\"" + out + "\"";
  }

//+------------------------------------------------------------------+
//| Write the whole CSV (temp file + atomic move).                   |
//| Returns rows written, -1 on file error, 0 on empty calendar.     |
//+------------------------------------------------------------------+
int ExportToFile(const string filename)
  {
   // Query the calendar FIRST: opening/truncating the output before a
   // transient calendar failure (terminal just started, weekend) used to
   // destroy the previous good export and leave a header-only file.
   datetime from = TimeTradeServer() - InpDaysBack  * 86400;
   datetime to   = TimeTradeServer() + InpDaysAhead * 86400;

   MqlCalendarValue values[];
   // CalendarValueHistory returns bool, NOT a count - the row count comes
   // from ArraySize(). The old `int total = CalendarValueHistory(...)` was
   // always 1 on success, exporting exactly ONE event.
   ResetLastError();
   if(!CalendarValueHistory(values, from, to))
     {
      Print("CalendarExporter: CalendarValueHistory failed error=", GetLastError());
      return -1;
     }
   int total = ArraySize(values);
   if(total <= 0)
     {
      Print("CalendarExporter: no calendar values in window");
      return 0;
     }

   // Write to a temp file, then atomically move over the real name; allow
   // shared read so the Python poller never hits a sharing violation.
   string temp_name = filename + ".tmp";
   int    file = FileOpen(temp_name, FILE_WRITE | FILE_TXT | FILE_UTF8 | FILE_COMMON | FILE_SHARE_READ);
   if(file == INVALID_HANDLE)
     {
      Print("CalendarExporter: cannot open ", temp_name, " error=", GetLastError());
      return -1;
     }

   FileWrite(file, "time_utc,currency,title,impact,actual,forecast,previous");

   int written = 0;
   for(int i = 0; i < total; i++)
     {
      MqlCalendarEvent   event;
      MqlCalendarCountry country;
      if(!CalendarEventById(values[i].event_id, event))
         continue;
      if(!CalendarCountryById(event.country_id, country))
         continue;

      string title    = event.name;
      string impact   = ImportanceToString(event.importance);
      string when     = FormatIsoUtc((datetime)values[i].time);
      string actual   = ValueToString(values[i].actual_value);
      string forecast = ValueToString(values[i].forecast_value);
      string previous = ValueToString(values[i].previous_value);
      string currency = country.currency;

      string line = when + "," + CsvQuote(currency) + "," + CsvQuote(title) + "," +
                    impact + "," + actual + "," + forecast + "," + previous;
      FileWrite(file, line);
      written++;
     }

   FileClose(file);

   if(!FileMove(temp_name, filename, FILE_REWRITE | FILE_COMMON))
     {
      Print("CalendarExporter: cannot publish ", filename, " error=", GetLastError());
      FileDelete(temp_name, FILE_COMMON);
      return -1;
     }
   return written;
  }

//+------------------------------------------------------------------+
//| Timer: rewrite the CSV every InpRefreshMin minutes.              |
//+------------------------------------------------------------------+
void OnTimer()
  {
   datetime now = TimeTradeServer();
   if(now - g_lastWrite < InpRefreshMin * 60)
      return;
   int written = ExportToFile(InpFileName);
   // Record the attempt time only after a SUCCESSFUL export so a failed
   // write retries on the next 15 s timer tick instead of waiting a full
   // refresh interval.
   if(written >= 0)
     {
      g_lastWrite = now;
      Print("CalendarExporter: wrote ", written, " rows to ", InpFileName);
     }
  }

//+------------------------------------------------------------------+
//| Init: export immediately, then check every 15 s.                 |
//+------------------------------------------------------------------+
int OnInit()
  {
   EventSetTimer(15);
   g_lastWrite = 0;                    // force first write
   OnTimer();
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
//| Deinit.                                                          |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   EventKillTimer();
  }
