#include "kpm/measurement-csv.h"
#include <iomanip>
#include <stdexcept>
namespace hero::oran {
MeasurementCsvWriter::MeasurementCsvWriter(const std::string& path):m_file(path) {
  if(!m_file) throw std::runtime_error("Cannot open measurement CSV: "+path);
  m_file<<"sequence,begin_sim_ns,end_sim_ns,gnb_id,simulation_node_id,cell_id,bwp_index,scope,imsi,rnti,generation,active_ues,connected_ues,available_prbs,prbs_per_rbg,symbols_per_slot,gnb_pdcp_attached,ue_pdcp_attached,unmapped_events,dl_cqi_sum,dl_cqi_samples";
  m_file<<",applied_embb_ratio,applied_urllc_ratio,applied_mmtc_ratio";
  for(const auto* direction:{"dl","ul"}) for(const auto* kind:{"used","budget"})
    for(const auto* slice:{"embb","urllc","mmtc"}) m_file<<','<<direction<<"_slice_"<<kind<<'_'<<slice;
  m_file<<",slice_id,slice_type,application_type,qos_profile,offered_rate_bps,required_throughput_bps,packet_size_bytes,packet_interval_us,delay_target_ms,reliability_target";
  for(const auto &prefix:{"dl","ul"}) {
    for(const auto &field:{"tx_pdcp_pdu_bytes","tx_pdcp_sdu_bytes","tx_pdcp_packets","rx_pdcp_pdu_bytes","rx_pdcp_sdu_bytes","rx_pdcp_packets","tx_rlc_pdu_bytes","tx_rlc_packets","rx_rlc_pdu_bytes","rx_rlc_packets","pdcp_rx_delay_ns_sum","rlc_rx_delay_ns_sum","allocated_prb_grant_sum","allocated_prb_symbols","data_grants","mac_scheduling_events","mac_scheduled_tb_bytes","mcs_sum","mcs_samples","sinr_linear_sum","sinr_samples","delivered_sdu_throughput_mbps","pdcp_delay_mean_ns","rlc_delay_mean_ns"}) m_file<<','<<prefix<<'_'<<field;
  }
  m_file<<'\n'<<std::setprecision(17);
}
void MeasurementCsvWriter::Write(const NativeMeasurementSnapshot& s,
    const std::optional<hero::slicing::SliceBudgetManager::WindowStats>& sliceStats) {
  double seconds=(s.endSimulationNs-s.beginSimulationNs)*1e-9;
  for(const auto &r:s.records) {
    m_file<<s.sequence<<','<<s.beginSimulationNs<<','<<s.endSimulationNs<<','<<s.node.gnbId<<','<<s.node.simulationNodeId<<','<<r.cellId<<','<<unsigned(r.bwpIndex)<<','<<(r.ue?"ue":"cell")<<',';
    if(r.ue) m_file<<r.ue->imsi;
    m_file<<','; if(r.ue) m_file<<r.ue->rnti;
    m_file<<','<<r.contextGeneration<<','<<(r.ue?unsigned(r.activeInWindow):r.activeUes)<<','<<(r.ue?unsigned(r.connectedAtEnd):r.connectedUes)<<','<<r.availablePrbs<<','<<r.prbsPerRbg<<','<<r.symbolsPerSlot<<','<<r.gnbPdcpAttached<<','<<r.uePdcpAttached<<','<<s.unmappedEvents<<','<<r.dlCqiSum<<','<<r.dlCqiSamples;
    if(!r.ue && sliceStats) {
      for(double ratio:sliceStats->appliedRatios) m_file<<','<<ratio;
      for(std::size_t direction=0;direction<2;++direction)
        for(std::size_t kind=0;kind<2;++kind)
          for(std::size_t slice=0;slice<3;++slice)
            m_file<<','<<(kind==0?sliceStats->used[direction][slice]:sliceStats->budgets[direction][slice]);
    } else {
      for(std::size_t column=0;column<15;++column) m_file<<',';
    }
    m_file<<','<<(r.ue?unsigned(r.ue->sliceId):0)<<','<<(r.ue?r.ue->sliceType:"")<<','<<(r.ue?r.ue->applicationType:"")<<','<<(r.ue?r.ue->qosProfile:"")<<','<<(r.ue?r.ue->offeredRateBps:0)<<','<<(r.ue?r.ue->requiredThroughputBps:0)<<','<<(r.ue?r.ue->packetSizeBytes:0)<<','<<(r.ue?r.ue->packetIntervalUs:0)<<','<<(r.ue?r.ue->delayTargetMs:0)<<','<<(r.ue?r.ue->reliabilityTarget:0);
    for(bool uplink:{false,true}) {
      const auto &d=uplink?r.ul:r.dl;
      m_file<<','<<d.txPdcpPduBytes<<','<<d.txPdcpSduBytes<<','<<d.txPdcpPackets<<','<<d.rxPdcpPduBytes<<','<<d.rxPdcpSduBytes<<','<<d.rxPdcpPackets
        <<','<<d.txRlcPduBytes<<','<<d.txRlcPackets<<','<<d.rxRlcPduBytes<<','<<d.rxRlcPackets<<','<<d.pdcpRxDelayNsSum<<','<<d.rlcRxDelayNsSum
        <<','<<d.allocatedPrbGrantSum<<','<<d.allocatedPrbSymbols<<','<<d.dataGrants<<','<<d.macSchedulingEvents<<','<<d.macScheduledTbBytes
        <<','<<d.mcsSum<<','<<d.mcsSamples<<','<<d.sinrLinearSum<<','<<d.sinrSamples<<',';
      bool receiver=uplink?r.gnbPdcpAttached:r.uePdcpAttached;
      if(receiver && seconds>0) m_file<<d.rxPdcpSduBytes*8e-6/seconds;
      m_file<<','; if(d.rxPdcpPackets) m_file<<double(d.pdcpRxDelayNsSum)/d.rxPdcpPackets;
      m_file<<','; if(d.rxRlcPackets) m_file<<double(d.rlcRxDelayNsSum)/d.rxRlcPackets;
    }
    m_file<<'\n';
  }
  m_file.flush(); if(!m_file) throw std::runtime_error("Measurement CSV write failed");
}
}
