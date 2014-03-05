#ifndef SKYLARK_FRFT_DATA_HPP
#define SKYLARK_FRFT_DATA_HPP

#include <vector>

#include "context.hpp"
#include "transform_data.hpp"
#include "dense_transform_data.hpp"
#include "../utility/randgen.hpp"

namespace skylark { namespace sketch {

namespace bstrand = boost::random;

/**
 * Fast Random Features Transform (data)
 *
 * Sketch transform into Eucledian space of fuctions in an RKHS
 * implicitly defined by a vector and a shift invaraint kernel. Fast variant
 * (also known as Fastfood).
 *
 * See:
 * Q. Le, T. Sarlos, A. Smola
 * Fastfood - Approximating Kernel Expansions in Loglinear Time
 * ICML 2013.
 */
template< typename ValueType >
struct FastRFT_data_t : public transform_data_t {

    typedef ValueType value_type;
    typedef transform_data_t base_t;

    /**
     * Regular constructor
     */
    FastRFT_data_t (int N, int S, skylark::sketch::context_t& context,
                    std::string name)
        : base_t(N, S, context, name),
          numblks(1 + ((base_t::S - 1) / base_t::N)),
          scale(std::sqrt(2.0 / base_t::S)),
          Sm(numblks * base_t::N)  {

        _populate();
    }

    FastRFT_data_t (boost::property_tree::ptree &json,
                    skylark::sketch::context_t& context)
        : base_t(json, context),
          numblks(1 + ((base_t::S - 1) / base_t::N)),
          scale(std::sqrt(2.0 / base_t::S)),
          Sm(numblks * base_t::N)  {

        _populate();
    }


protected:
    const int numblks;
    const value_type scale; /** Scaling for trigonometric factor */
    std::vector<value_type> Sm; /** Scaling based on kernel (filled by subclass) */
    std::vector<value_type> B;
    std::vector<value_type> G;
    std::vector<int> P;
    std::vector<value_type> shifts; /** Shifts for scaled trigonometric factor */


    void _populate() {
        const double pi = boost::math::constants::pi<value_type>();
        bstrand::uniform_real_distribution<value_type> dist_shifts(0, 2 * pi);
        shifts = context.generate_random_samples_array(base_t::S, dist_shifts);
        utility::rademacher_distribution_t<value_type> dist_B;
        B = context.generate_random_samples_array(numblks * base_t::N, dist_B);
        bstrand::normal_distribution<value_type> dist_G;
        G = context.generate_random_samples_array(numblks * base_t::N, dist_G);

        // For the permutation we use Fisher-Yates (Knuth)
        // The following will generate the indexes for the swaps. However
        // the scheme here might have a small bias if N is small
        // (has to be really small).
        bstrand::uniform_int_distribution<int> dist_P(0);
        P = context.generate_random_samples_array(numblks * (base_t::N - 1), dist_P);
        for(int i = 0; i < numblks; i++)
            for(int j = base_t::N - 1; j >= 1; j--)
                P[i * (base_t::N - 1) + base_t::N - 1 - j] =
                    P[i * (base_t::N - 1) + base_t::N - 1 - j] % (j + 1);

        // Fill scaling matrix with 1. Subclasses (which are adapted to concrete
        // kernels) should modify this.
        std::fill(Sm.begin(), Sm.end(), 1.0);
    }
};

template<typename ValueType>
struct FastGaussianRFT_data_t :
        public FastRFT_data_t<ValueType> {

    typedef FastRFT_data_t<ValueType> base_t;
    typedef typename base_t::value_type value_type;

    /**
     * Constructor
     * Most of the work is done by base. Here just write scale
     */
    FastGaussianRFT_data_t(int N, int S, value_type sigma,
        skylark::sketch::context_t& context)
        : base_t(N, S, context, "FastGaussianRFT"), _sigma(sigma) {

        std::fill(base_t::Sm.begin(), base_t::Sm.end(),
                1.0 / (_sigma * std::sqrt(base_t::N)));
    }

    FastGaussianRFT_data_t(boost::property_tree::ptree &json,
                           skylark::sketch::context_t& context)
        : base_t(json, context),
        _sigma(json.get<value_type>("sketch.sigma")) {

        std::fill(base_t::Sm.begin(), base_t::Sm.end(),
                1.0 / (_sigma * std::sqrt(base_t::N)));
    }

    template <typename ValueT>
    friend boost::property_tree::ptree& operator<<(
        boost::property_tree::ptree &sk,
        const FastGaussianRFT_data_t<ValueT> &data);

protected:
    const value_type _sigma; /**< Bandwidth (sigma)  */

};

template <typename ValueType>
boost::property_tree::ptree& operator<<(
        boost::property_tree::ptree &sk,
        const FastGaussianRFT_data_t<ValueType> &data) {

    sk << static_cast<const transform_data_t&>(data);
    sk.put("sketch.sigma", data._sigma);
    return sk;
}

} } /** namespace skylark::sketch */

#endif /** SKYLARK_FRFT_DATA_HPP */
