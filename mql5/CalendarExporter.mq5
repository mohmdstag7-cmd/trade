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
//| Install: MetaEditor -> compile -> attach to ANY chart. Read-only |
//| EA: no trading permission is required.                           |
//+------------------------------------------------------------------+
#property copyright "MT5 Trading Workstation"
#property version   "1.00"
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
//| Server-time ISO string: "2024-03-12T13:30:00Z".                  |
//| (Calendar times follow the terminal's server clock; the app      |
//| pairs this file with its broker-offset detection - SPEC C2.4.)   |
//+------------------------------------------------------------------+
string FormatIso(datetime t)
  {
   string day  = TimeToString(t, TIME_DATE);     // "2024.03.12"
   string time = TimeToString(t, TIME_MINUTES);  // "13:30"
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
//| Write the whole CSV. Returns rows written, -1 on file error.     |
//+------------------------------------------------------------------+
int ExportToFile(const string filename)
  {
   int file = FileOpen(filename, FILE_WRITE | FILE_TXT | FILE_UTF8 | FILE_COMMON);
   if(file == INVALID_HANDLE)
     {
      Print("CalendarExporter: cannot open ", filename, " error=", GetLastError());
      return -1;
     }

   FileWrite(file, "time_utc,currency,title,impact,actual,forecast,previous");

   datetime from = TimeTradeServer() - InpDaysBack  * 86400;
   datetime to   = TimeTradeServer() + InpDaysAhead * 86400;

   MqlCalendarValue values[];
   int total = CalendarValueHistory(values, from, to);
   if(total <= 0)
     {
      FileClose(file);
      Print("CalendarExporter: no calendar values (total=", total, ")");
      return 0;
     }

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
      string when     = FormatIso((datetime)values[i].time);
      string actual   = ValueToString(values[i].actual_value);
      string forecast = ValueToString(values[i].forecast_value);
      string previous = ValueToString(values[i].previous_value);
      string currency = country.currency;

      string line = when + "," + currency + ",\"" + title + "\"," +
                    impact + "," + actual + "," + forecast + "," + previous;
      FileWrite(file, line);
      written++;
     }

   FileClose(file);
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
   g_lastWrite = now;
   int written = ExportToFile(InpFileName);
   if(written >= 0)
      Print("CalendarExporter: wrote ", written, " rows to ", InpFileName);
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
